"""Loopback listener so the Pascal editor can hand a scene straight to Blender.

A tiny HTTP server on 127.0.0.1 accepts a GLB from an allowed web origin, parks it
in a temp file, and a `bpy.app.timers` callback imports it on Blender's main thread
(bpy is not thread-safe). Browsers on an https page may fetch loopback, but Chrome
requires the preflight to carry `Access-Control-Allow-Private-Network`.

Routes:
    OPTIONS *                  CORS / private-network preflight
    GET     /pascal/health     {app, version, addon, port, allowed}
    POST    /pascal/import     body model/gltf-binary -> 202 {id, state}
    GET     /pascal/import/ID  {id, state, summary?, error?}
"""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import bpy

from .importer import import_pascal_file

DEFAULT_PORT = 27412
PORT_RANGE = 5
MAX_BODY_BYTES = 512 * 1024 * 1024
HEALTH_PATH = "/pascal/health"
IMPORT_PATH = "/pascal/import"
ALLOWED_HEADERS = "Content-Type, X-Pascal-Project-Name, X-Pascal-Project-Id, X-Pascal-Version"
DEFAULT_ALLOWED_ORIGINS = ("https://editor.pascal.app",)
DRAIN_INTERVAL = 0.25
JOB_TTL = 15 * 60


def addon_version() -> str:
    try:
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as handle:
            return str(tomllib.load(handle).get("version", "0"))
    except (OSError, tomllib.TOMLDecodeError):
        return "0"


@dataclass
class Job:
    id: str
    path: str
    name: str
    project_id: str | None
    version: str | None
    origin: str | None
    state: str = "queued"
    summary: str | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)

    def payload(self) -> dict:
        data = {"id": self.id, "state": self.state}
        if self.summary:
            data["summary"] = self.summary
        if self.error:
            data["error"] = self.error
        return data


class _State:
    def __init__(self) -> None:
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int | None = None
        self.jobs: dict[str, Job] = {}
        self.queue: queue.Queue[Job] = queue.Queue()
        self.pending_origins: dict[str, float] = {}
        self.runtime_allowed: set[str] = set()
        self.lock = threading.Lock()
        self.last_error: str | None = None


STATE = _State()


# --- preferences ------------------------------------------------------------


def _prefs():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def _normalize(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def allowed_origins() -> list[str]:
    prefs = _prefs()
    raw = prefs.allowed_origins if prefs is not None else ",".join(DEFAULT_ALLOWED_ORIGINS)
    listed = [_normalize(o) for o in raw.replace("\n", ",").split(",") if o.strip()]
    with STATE.lock:
        extra = sorted(STATE.runtime_allowed)
    return list(dict.fromkeys(listed + extra))


def is_origin_allowed(origin: str | None) -> bool:
    # No Origin header means a non-browser client on this machine; the browser is the
    # only ambient-authority caller the allowlist exists to gate.
    if not origin:
        return True
    return _normalize(origin) in allowed_origins()


def pending_origins() -> list[str]:
    with STATE.lock:
        return sorted(STATE.pending_origins)


def _note_pending(origin: str) -> None:
    with STATE.lock:
        STATE.pending_origins[_normalize(origin)] = time.time()


def allow_origin(origin: str) -> None:
    origin = _normalize(origin)
    prefs = _prefs()
    if prefs is not None:
        current = [o for o in allowed_origins() if o not in STATE.runtime_allowed]
        if origin not in current:
            prefs.allowed_origins = ", ".join(current + [origin])
    else:
        with STATE.lock:
            STATE.runtime_allowed.add(origin)
    with STATE.lock:
        STATE.pending_origins.pop(origin, None)


def forget_pending(origin: str) -> None:
    with STATE.lock:
        STATE.pending_origins.pop(_normalize(origin), None)


# --- http ---------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = "PascalBlender"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args) -> None:  # noqa: A002 — BaseHTTPRequestHandler signature
        return

    def _origin(self) -> str | None:
        origin = self.headers.get("Origin")
        return origin.rstrip("/") if origin else None

    def _cors(self, origin: str | None) -> None:
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", ALLOWED_HEADERS)
        if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")

    def _json(self, status: int, payload: dict, origin: str | None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self._cors(origin)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _discard_body(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        while length > 0:
            chunk = self.rfile.read(min(length, 1 << 16))
            if not chunk:
                break
            length -= len(chunk)

    def do_OPTIONS(self) -> None:  # noqa: N802 — http.server naming
        self.send_response(204)
        self._cors(self._origin())
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        origin = self._origin()
        path = urlsplit(self.path).path
        if path == HEALTH_PATH:
            allowed = is_origin_allowed(origin)
            if origin and not allowed:
                _note_pending(origin)
            self._json(
                200,
                {
                    "app": "blender",
                    "version": bpy.app.version_string,
                    "addon": addon_version(),
                    "port": STATE.port,
                    "allowed": allowed,
                },
                origin,
            )
            return
        if path.startswith(IMPORT_PATH + "/"):
            with STATE.lock:
                job = STATE.jobs.get(path[len(IMPORT_PATH) + 1 :])
            if job is None:
                self._json(404, {"error": "unknown_import"}, origin)
            else:
                self._json(200, job.payload(), origin)
            return
        self._json(404, {"error": "not_found"}, origin)

    def do_POST(self) -> None:  # noqa: N802
        origin = self._origin()
        if urlsplit(self.path).path != IMPORT_PATH:
            self._discard_body()
            self._json(404, {"error": "not_found"}, origin)
            return
        if not is_origin_allowed(origin):
            _note_pending(origin)
            self._discard_body()
            self._json(403, {"error": "origin_not_allowed", "origin": origin}, origin)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._discard_body()
            self._json(413 if length > MAX_BODY_BYTES else 411, {"error": "bad_length"}, origin)
            return

        handle, path = tempfile.mkstemp(prefix="pascal-", suffix=".glb")
        remaining = length
        magic = b""
        try:
            with os.fdopen(handle, "wb") as out:
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        raise ConnectionError("client stopped sending")
                    if len(magic) < 4:
                        magic += chunk[: 4 - len(magic)]
                    out.write(chunk)
                    remaining -= len(chunk)
        except (OSError, ConnectionError) as error:
            os.unlink(path)
            self._json(400, {"error": "upload_failed", "detail": str(error)}, origin)
            return
        if magic != b"glTF":
            os.unlink(path)
            self._json(400, {"error": "not_glb"}, origin)
            return

        job = Job(
            id=uuid.uuid4().hex,
            path=path,
            name=unquote(self.headers.get("X-Pascal-Project-Name") or "") or "Pascal scene",
            project_id=self.headers.get("X-Pascal-Project-Id"),
            version=self.headers.get("X-Pascal-Version"),
            origin=origin,
        )
        with STATE.lock:
            STATE.jobs[job.id] = job
        STATE.queue.put(job)
        self._json(202, job.payload(), origin)


# --- lifecycle ----------------------------------------------------------------


def running() -> bool:
    return STATE.server is not None


def port() -> int | None:
    return STATE.port


def start(port: int | None = None) -> int:
    """Bind the listener. `port=None` takes the preference (with a short fallback
    range); `port=0` asks the OS for a free one."""
    if STATE.server is not None:
        return STATE.port or 0
    prefs = _prefs()
    wanted = DEFAULT_PORT if prefs is None else int(prefs.port)
    if port is not None:
        wanted = port
    candidates = [wanted] if wanted == 0 or port is not None else list(range(wanted, wanted + PORT_RANGE))
    last: OSError | None = None
    for candidate in candidates:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), _Handler)
            break
        except OSError as error:
            last = error
    else:
        STATE.last_error = str(last)
        raise OSError(f"no free port in {candidates}: {last}")
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="pascal-listener", daemon=True)
    thread.start()
    STATE.server, STATE.thread, STATE.port, STATE.last_error = server, thread, server.server_address[1], None
    if not bpy.app.timers.is_registered(drain_queue):
        bpy.app.timers.register(drain_queue, first_interval=DRAIN_INTERVAL, persistent=True)
    return STATE.port


def stop() -> None:
    server, STATE.server, STATE.thread, STATE.port = STATE.server, None, None, None
    if server is not None:
        server.shutdown()
        server.server_close()


def drain_queue() -> float | None:
    """Timer callback: import queued files on the main thread. Returns the next
    interval, or None (unregister) once the listener is stopped and idle."""
    processed = False
    while True:
        try:
            job = STATE.queue.get_nowait()
        except queue.Empty:
            break
        processed = True
        try:
            summary = import_pascal_file(bpy.context, job.path, scene_name=job.name)
            job.state, job.summary = "done", summary.describe()
            try:
                bpy.context.window_manager.pascal_last_import = job.summary
            except AttributeError:
                pass
        except Exception as error:  # noqa: BLE001 — report whatever the importer raised
            job.state, job.error = "failed", str(error)
        finally:
            try:
                os.unlink(job.path)
            except OSError:
                pass
    if processed:
        _redraw()
    _expire_jobs()
    return DRAIN_INTERVAL if STATE.server is not None else None


def _expire_jobs() -> None:
    cutoff = time.time() - JOB_TTL
    with STATE.lock:
        for job_id in [j.id for j in STATE.jobs.values() if j.created < cutoff]:
            del STATE.jobs[job_id]


def _redraw() -> None:
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except AttributeError:
        pass


def _auto_start() -> None:
    prefs = _prefs()
    if prefs is not None and not prefs.auto_start:
        return None
    try:
        start()
    except OSError:
        pass
    return None


def register() -> None:
    if not bpy.app.background:
        bpy.app.timers.register(_auto_start, first_interval=0.5)


def unregister() -> None:
    stop()
    if bpy.app.timers.is_registered(drain_queue):
        bpy.app.timers.unregister(drain_queue)
