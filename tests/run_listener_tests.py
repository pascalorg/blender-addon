"""Headless checks for the loopback listener.

    blender --background --factory-startup --python tests/run_listener_tests.py -- [fixture.glb]
"""

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "pascal-sample.glb")
EDITOR = "https://editor.pascal.app"
STRANGER = "https://stranger.example"


def load_addon():
    spec = importlib.util.spec_from_file_location(
        "pascal_addon", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pascal_addon"] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def request(base, method, path, origin=None, body=None, headers=None):
    req = urllib.request.Request(base + path, data=body, method=method)
    if origin:
        req.add_header("Origin", origin)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read()
            return response.status, dict(response.headers), payload
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    fixture = argv[0] if argv else DEFAULT_FIXTURE
    load_addon()
    from pascal_addon import listener

    port = listener.start(port=0)
    assert listener.running() and port > 0
    base = f"http://127.0.0.1:{port}"

    # Preflight from the editor: CORS + private-network access headers.
    status, headers, _ = request(
        base, "OPTIONS", "/pascal/import", origin=EDITOR,
        headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Private-Network": "true"},
    )
    assert status == 204, status
    assert headers.get("Access-Control-Allow-Origin") == EDITOR, headers
    assert headers.get("Access-Control-Allow-Private-Network") == "true", headers
    assert "X-Pascal-Project-Name" in headers.get("Access-Control-Allow-Headers", ""), headers

    # Health answers everyone, but says whether the origin may send.
    status, _, body = request(base, "GET", "/pascal/health", origin=EDITOR)
    health = json.loads(body)
    assert status == 200 and health["app"] == "blender" and health["allowed"] is True, health
    status, _, body = request(base, "GET", "/pascal/health", origin=STRANGER)
    assert json.loads(body)["allowed"] is False
    assert STRANGER in listener.pending_origins(), listener.pending_origins()

    # A stranger cannot import; the editor can.
    with open(fixture, "rb") as handle:
        glb = handle.read()
    status, _, body = request(base, "POST", "/pascal/import", origin=STRANGER, body=glb,
                              headers={"Content-Type": "model/gltf-binary"})
    assert status == 403 and json.loads(body)["error"] == "origin_not_allowed", (status, body)

    status, _, body = request(base, "POST", "/pascal/import", origin=EDITOR, body=b"nope",
                              headers={"Content-Type": "model/gltf-binary"})
    assert status == 400 and json.loads(body)["error"] == "not_glb", (status, body)

    status, _, body = request(
        base, "POST", "/pascal/import", origin=EDITOR, body=glb,
        headers={"Content-Type": "model/gltf-binary", "X-Pascal-Project-Name": "Sample%20house%20%C3%A9t%C3%A9",
                 "X-Pascal-Project-Id": "project_sample"},
    )
    job = json.loads(body)
    assert status == 202 and job["state"] == "queued", (status, body)
    temp_path = listener.STATE.jobs[job["id"]].path
    assert os.path.exists(temp_path)

    # The main-thread timer does the import (called directly here: no event loop in --background).
    objects_before = len(bpy.data.objects)
    listener.drain_queue()
    status, _, body = request(base, "GET", f"/pascal/import/{job['id']}", origin=EDITOR)
    done = json.loads(body)
    assert status == 200 and done["state"] == "done", done
    assert "Sample house été" in done["summary"], done
    assert len(bpy.data.objects) > objects_before
    assert not os.path.exists(temp_path), "temp file not removed"
    assert bpy.data.collections.get("Sample house été") is not None

    # Approving a pending origin lets it send; a second send replaces the first.
    listener.allow_origin(STRANGER)
    assert STRANGER not in listener.pending_origins()
    status, _, body = request(base, "POST", "/pascal/import", origin=STRANGER, body=glb,
                              headers={"Content-Type": "model/gltf-binary", "X-Pascal-Project-Name": "Sample%20house%20%C3%A9t%C3%A9"})
    assert status == 202, (status, body)
    count = len(bpy.data.objects)
    listener.drain_queue()
    assert len(bpy.data.objects) == count, "re-send did not replace"
    assert json.loads(request(base, "GET", f"/pascal/import/{json.loads(body)['id']}")[2])["state"] == "done"

    status, _, _ = request(base, "GET", "/pascal/import/nope")
    assert status == 404

    listener.stop()
    assert not listener.running()
    print("OK listener on port", port)


if __name__ == "__main__":
    main()
