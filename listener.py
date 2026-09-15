"""Loopback listener for the Pascal editor, hosted in a helper process.

`bpy` is main-thread only, so the add-on opens no socket and starts no thread: it
launches `server.py` with Blender's bundled Python (`subprocess`) and swaps files with
it through a spool folder. A `bpy.app.timers` callback picks up whatever arrived and
imports it on Blender's main thread.

Spool layout — one writer per file, every write is rename-into-place:

    config.json               add-on -> helper: allowed and dismissed origins
    heartbeat                 add-on -> helper: touched each tick; stale means Blender quit
    runtime.json              helper -> add-on: the bound port, or the bind error
    pending.json              helper -> add-on: origins that asked and were turned away
    incoming/<id>.glb|.json   helper -> add-on: a scene waiting to be imported
    results/<id>.json         add-on -> helper: how that import went

Wire protocol (served by the helper, see `server.py`):
    GET  /pascal/health        {app, version, addon, port, allowed}
    POST /pascal/import        body model/gltf-binary -> 202 {id, state}
    GET  /pascal/import/ID     {id, state, summary?, error?}
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

import bpy

from .importer import import_pascal_file

DEFAULT_PORT = 27412
PORT_RANGE = 5
DEFAULT_ALLOWED_ORIGINS = ("https://editor.pascal.app",)
DRAIN_INTERVAL = 0.25
STARTUP_TIMEOUT = 8.0
JOB_TTL = 15 * 60
SERVER_SCRIPT = os.path.join(os.path.dirname(__file__), "server.py")


def addon_version() -> str:
    try:
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as handle:
            return str(tomllib.load(handle).get("version", "0"))
    except (OSError, tomllib.TOMLDecodeError):
        return "0"


class _State:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.spool: str | None = None
        self.port: int | None = None
        self.pending: list[str] = []
        self.pending_stamp: float = 0.0
        self.dismissed: set[str] = set()
        self.runtime_allowed: set[str] = set()
        self.last_error: str | None = None


STATE = _State()


# --- spool paths --------------------------------------------------------------


def _path(*parts: str) -> str:
    if STATE.spool is None:
        raise RuntimeError("the listener is not running")
    return os.path.join(STATE.spool, *parts)


def _write_json(path: str, payload: dict) -> None:
    temp = path + ".part"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(temp, path)


def _read_json(path: str) -> dict:
    try:
        with open(path, "rb") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# --- preferences and origins --------------------------------------------------


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
    return list(dict.fromkeys(listed + sorted(STATE.runtime_allowed)))


def publish_config() -> None:
    """Hand the helper the current allowlist. Cheap enough to call on every change."""
    if STATE.spool is None:
        return
    _write_json(
        _path("config.json"),
        {
            "allowed": allowed_origins(),
            "dismissed": sorted(STATE.dismissed),
            "blender": bpy.app.version_string,
            "addon": addon_version(),
        },
    )


def is_origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    return _normalize(origin) in allowed_origins()


def pending_origins() -> list[str]:
    return list(STATE.pending)


def allow_origin(origin: str) -> None:
    origin = _normalize(origin)
    prefs = _prefs()
    if prefs is not None:
        current = [o for o in allowed_origins() if o not in STATE.runtime_allowed]
        if origin not in current:
            prefs.allowed_origins = ", ".join(current + [origin])
    else:
        STATE.runtime_allowed.add(origin)
    _drop_pending(origin)


def forget_pending(origin: str) -> None:
    _drop_pending(_normalize(origin))


def _drop_pending(origin: str) -> None:
    STATE.dismissed.add(origin)
    STATE.pending = [o for o in STATE.pending if o != origin]
    publish_config()


def _refresh_pending() -> bool:
    """Re-read what the helper says is knocking. True when the list changed."""
    path = _path("pending.json")
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return False
    if stamp == STATE.pending_stamp:
        return False
    STATE.pending_stamp = stamp
    allowed = set(allowed_origins())
    pending = sorted(o for o in _read_json(path) if o not in allowed and o not in STATE.dismissed)
    if pending == STATE.pending:
        return False
    STATE.pending = pending
    return True


# --- lifecycle ----------------------------------------------------------------


def running() -> bool:
    return STATE.process is not None and STATE.process.poll() is None


def port() -> int | None:
    return STATE.port


def _python() -> str:
    """Blender's bundled interpreter. `sys.executable` is it on every stock build;
    the fallbacks cover distro builds that run Blender against a system Python."""
    candidates = [sys.executable, getattr(sys, "_base_executable", None)]
    for candidate in candidates:
        if candidate and os.path.basename(candidate).lower().startswith("python") and os.path.exists(candidate):
            return candidate
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    raise OSError("no Python interpreter to run the Pascal listener with")


def _touch_heartbeat() -> None:
    path = _path("heartbeat")
    try:
        with open(path, "a"):
            os.utime(path, None)
    except OSError:
        pass


def _log_tail(limit: int = 400) -> str:
    try:
        with open(_path("server.log"), encoding="utf-8", errors="replace") as handle:
            return handle.read()[-limit:].strip()
    except OSError:
        return ""


def start(port: int | None = None) -> int:
    """Launch the helper. `port=None` takes the preference (with a short fallback
    range); `port=0` asks the OS for a free one."""
    if running():
        return STATE.port or 0
    stop()

    prefs = _prefs()
    wanted = DEFAULT_PORT if prefs is None else int(prefs.port)
    span = PORT_RANGE
    if port is not None:
        wanted, span = port, 1

    STATE.spool = tempfile.mkdtemp(prefix="pascal-blender-")
    os.mkdir(_path("incoming"))
    os.mkdir(_path("results"))
    publish_config()
    _touch_heartbeat()

    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with open(_path("server.log"), "w", encoding="utf-8") as log:
        process = subprocess.Popen(  # noqa: S603 — our own script, our own interpreter
            [_python(), SERVER_SCRIPT, "--spool", STATE.spool, "--port", str(wanted), "--range", str(span)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(SERVER_SCRIPT),
            **flags,
        )
    STATE.process = process

    runtime_path = _path("runtime.json")
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        runtime = _read_json(runtime_path)
        if runtime.get("port"):
            STATE.port, STATE.last_error = int(runtime["port"]), None
            if not bpy.app.timers.is_registered(drain_spool):
                bpy.app.timers.register(drain_spool, first_interval=DRAIN_INTERVAL, persistent=True)
            return STATE.port
        if runtime.get("error") or process.poll() is not None:
            break
        time.sleep(0.02)

    detail = _read_json(runtime_path).get("error") or _log_tail() or "the listener process did not answer"
    stop()
    STATE.last_error = str(detail)
    raise OSError(STATE.last_error)


def stop() -> None:
    process, STATE.process = STATE.process, None
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    spool, STATE.spool = STATE.spool, None
    if spool:
        shutil.rmtree(spool, ignore_errors=True)
    STATE.port = None
    STATE.pending = []
    STATE.pending_stamp = 0.0


def drain_spool() -> float | None:
    """Timer callback: import whatever the helper spooled, on the main thread. Returns
    the next interval, or None (unregister) once the listener is stopped."""
    if STATE.spool is None:
        return None
    if STATE.process is not None and STATE.process.poll() is not None:
        STATE.last_error = _log_tail() or "the listener process stopped"
        stop()
        _redraw()
        return None

    _touch_heartbeat()
    changed = _refresh_pending()
    for job in _collect_jobs():
        _run_job(job)
        changed = True
    if changed:
        _redraw()
    _expire_results()
    return DRAIN_INTERVAL


def _collect_jobs() -> list[dict]:
    """Meta files land after their GLB, so a readable meta file means the pair is whole."""
    folder = _path("incoming")
    try:
        names = sorted(name for name in os.listdir(folder) if name.endswith(".json"))
    except OSError:
        return []
    jobs = []
    for name in names:
        meta = _read_json(os.path.join(folder, name))
        job_id = meta.get("id") or name[:-5]
        glb = os.path.join(folder, f"{job_id}.glb")
        if not os.path.exists(glb):
            _unlink(os.path.join(folder, name))
            continue
        jobs.append({"id": job_id, "path": glb, "meta": os.path.join(folder, name), "name": meta.get("name") or "Pascal scene"})
    return jobs


def _run_job(job: dict) -> None:
    try:
        summary = import_pascal_file(bpy.context, job["path"], scene_name=job["name"])
        result = {"state": "done", "summary": summary.describe()}
        try:
            bpy.context.window_manager.pascal_last_import = result["summary"]
        except AttributeError:
            pass
    except Exception as error:  # noqa: BLE001 — report whatever the importer raised
        result = {"state": "failed", "error": str(error)}
    finally:
        _unlink(job["path"])
        _unlink(job["meta"])
    _write_json(_path("results", f"{job['id']}.json"), result)


def _expire_results() -> None:
    cutoff = time.time() - JOB_TTL
    folder = _path("results")
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                _unlink(entry.path)
        except OSError:
            pass


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
    if bpy.app.timers.is_registered(drain_spool):
        bpy.app.timers.unregister(drain_spool)
