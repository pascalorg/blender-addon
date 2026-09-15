"""The loopback HTTP server, run as a helper process by the Pascal add-on.

Blender's `bpy` is main-thread only, so the add-on never opens a socket or starts a
thread of its own: it launches this script with Blender's bundled Python and talks to
it through a spool folder. This process owns the socket, writes each received GLB into
`incoming/`, and reads back what Blender made of it from `results/`.

Nothing here imports `bpy`; the only shared state is files.

    python server.py --spool DIR [--port N] [--range N]

Routes:
    OPTIONS *                  CORS / private-network preflight
    GET     /pascal/health     {app, version, addon, port, allowed}
    POST    /pascal/import     body model/gltf-binary -> 202 {id, state}
    GET     /pascal/import/ID  {id, state, summary?, error?}
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlsplit

HEALTH_PATH = "/pascal/health"
IMPORT_PATH = "/pascal/import"
ALLOWED_HEADERS = "Content-Type, X-Pascal-Project-Name, X-Pascal-Project-Id, X-Pascal-Version"
MAX_BODY_BYTES = 512 * 1024 * 1024
POLL_INTERVAL = 0.5
# How long the add-on may go without touching the heartbeat before we assume Blender
# is gone. The add-on touches it on every timer tick (four times a second).
HEARTBEAT_GRACE = 20.0
SOCKET_TIMEOUT = 30
JOB_TTL = 15 * 60


class Spool:
    """The folder the add-on and this process swap files through."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.config = os.path.join(root, "config.json")
        self.heartbeat = os.path.join(root, "heartbeat")
        self.runtime = os.path.join(root, "runtime.json")
        self.pending = os.path.join(root, "pending.json")
        self.incoming = os.path.join(root, "incoming")
        self.results = os.path.join(root, "results")

    def write_json(self, path: str, payload: dict) -> None:
        temp = path + ".part"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temp, path)

    def read_json(self, path: str) -> dict:
        try:
            with open(path, "rb") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def alive(self) -> bool:
        """False once Blender stopped touching the heartbeat (or took the spool away)."""
        try:
            return time.time() - os.path.getmtime(self.heartbeat) < HEARTBEAT_GRACE
        except OSError:
            return False


def normalize(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


class _Handler(BaseHTTPRequestHandler):
    server_version = "PascalBlender"
    protocol_version = "HTTP/1.1"
    timeout = SOCKET_TIMEOUT

    # --- plumbing ------------------------------------------------------------

    def log_message(self, format, *args) -> None:  # noqa: A002 — BaseHTTPRequestHandler signature
        return

    @property
    def spool(self) -> Spool:
        return self.server.spool

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
        # One request per connection: a single-threaded server must not be held open.
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _discard_body(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        while length > 0:
            chunk = self.rfile.read(min(length, 1 << 16))
            if not chunk:
                break
            length -= len(chunk)

    # --- allowlist -----------------------------------------------------------

    def _config(self) -> dict:
        return self.spool.read_json(self.spool.config)

    def _is_allowed(self, origin: str | None) -> bool:
        # No Origin header means a non-browser client on this machine; the browser is
        # the only ambient-authority caller the allowlist exists to gate.
        if not origin:
            return True
        allowed = self._config().get("allowed") or []
        return normalize(origin) in {normalize(str(entry)) for entry in allowed}

    def _note_pending(self, origin: str) -> None:
        origin = normalize(origin)
        config = self._config()
        if origin in {normalize(str(o)) for o in config.get("dismissed") or []}:
            return
        pending = self.spool.read_json(self.spool.pending)
        if origin in pending:
            return
        pending[origin] = time.time()
        self.spool.write_json(self.spool.pending, pending)

    # --- routes --------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 — http.server naming
        self.send_response(204)
        self._cors(self._origin())
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        origin = self._origin()
        path = urlsplit(self.path).path
        if path == HEALTH_PATH:
            allowed = self._is_allowed(origin)
            if origin and not allowed:
                self._note_pending(origin)
            config = self._config()
            self._json(
                200,
                {
                    "app": "blender",
                    "version": str(config.get("blender", "")),
                    "addon": str(config.get("addon", "")),
                    "port": self.server.server_address[1],
                    "allowed": allowed,
                },
                origin,
            )
            return
        if path.startswith(IMPORT_PATH + "/"):
            payload = self.server.job_payload(path[len(IMPORT_PATH) + 1 :])
            if payload is None:
                self._json(404, {"error": "unknown_import"}, origin)
            else:
                self._json(200, payload, origin)
            return
        self._json(404, {"error": "not_found"}, origin)

    def do_POST(self) -> None:  # noqa: N802
        origin = self._origin()
        if urlsplit(self.path).path != IMPORT_PATH:
            self._discard_body()
            self._json(404, {"error": "not_found"}, origin)
            return
        if not self._is_allowed(origin):
            if origin:
                self._note_pending(origin)
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

        job_id = uuid.uuid4().hex
        glb = os.path.join(self.spool.incoming, job_id + ".glb")
        temp = glb + ".part"
        remaining = length
        magic = b""
        try:
            with open(temp, "wb") as out:
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        raise ConnectionError("client stopped sending")
                    if len(magic) < 4:
                        magic += chunk[: 4 - len(magic)]
                    out.write(chunk)
                    remaining -= len(chunk)
        except (OSError, ConnectionError) as error:
            _unlink(temp)
            self._json(400, {"error": "upload_failed", "detail": str(error)}, origin)
            return
        if magic != b"glTF":
            _unlink(temp)
            self._json(400, {"error": "not_glb"}, origin)
            return

        os.replace(temp, glb)
        # The meta file lands last: its presence is what tells the add-on a scene is ready.
        self.spool.write_json(
            os.path.join(self.spool.incoming, job_id + ".json"),
            {
                "id": job_id,
                "name": unquote(self.headers.get("X-Pascal-Project-Name") or "") or "Pascal scene",
                "project_id": self.headers.get("X-Pascal-Project-Id"),
                "version": self.headers.get("X-Pascal-Version"),
                "origin": origin,
            },
        )
        self.server.jobs[job_id] = time.time()
        self._json(202, {"id": job_id, "state": "queued"}, origin)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class _Server(HTTPServer):
    """Single-threaded on purpose: one editor, one scene at a time, no threads."""

    def __init__(self, address, spool: Spool) -> None:
        super().__init__(address, _Handler)
        self.spool = spool
        self.jobs: dict[str, float] = {}

    def job_payload(self, job_id: str) -> dict | None:
        if not job_id or "/" in job_id or job_id != os.path.basename(job_id):
            return None
        done = self.spool.read_json(os.path.join(self.spool.results, job_id + ".json"))
        if done:
            return {"id": job_id, **{k: v for k, v in done.items() if k != "id"}}
        if job_id in self.jobs:
            return {"id": job_id, "state": "queued"}
        return None

    def expire_jobs(self) -> None:
        cutoff = time.time() - JOB_TTL
        for job_id in [key for key, created in self.jobs.items() if created < cutoff]:
            del self.jobs[job_id]


def bind(spool: Spool, wanted: int, span: int) -> _Server:
    candidates = [wanted] if wanted == 0 or span <= 1 else list(range(wanted, wanted + span))
    last: OSError | None = None
    for candidate in candidates:
        try:
            return _Server(("127.0.0.1", candidate), spool)
        except OSError as error:
            last = error
    raise OSError(f"no free port in {candidates}: {last}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spool", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--range", type=int, default=1)
    args = parser.parse_args()

    spool = Spool(args.spool)
    try:
        server = bind(spool, args.port, args.range)
    except OSError as error:
        spool.write_json(spool.runtime, {"error": str(error), "pid": os.getpid()})
        return 1

    server.timeout = POLL_INTERVAL
    spool.write_json(spool.runtime, {"port": server.server_address[1], "pid": os.getpid()})
    try:
        while spool.alive():
            server.handle_request()
            server.expire_jobs()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    # Blender went away without stopping us: take the spool folder with us.
    if not spool.alive():
        shutil.rmtree(spool.root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
