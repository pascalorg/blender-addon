# Pascal for Blender

Import the GLB files [Pascal](https://pascal.app) produces and get a structured, editable Blender
scene instead of a flat pile of meshes:

- one collection per level (`Ground Floor`, `First Floor`, …) with sub-collections per kind
  (`Walls`, `Doors`, `Windows`, `Items`, `Zones`, `Slabs`, `Ceilings`, …)
- objects named after their Pascal labels, with `pascalId` / `kind` / `label` kept as custom properties
- zones rebuilt as coloured floor polygons (viewport only, excluded from renders)
- the door and window `open` clips as NLA tracks on the moving parts
- metric units, 1 unit = 1 m
- re-importing the same project replaces the previous import instead of stacking `.001` copies
- a loopback listener so the editor's *Send to Blender* lands the live scene in the open Blender
- optional lighting on import (on by default): a physical sky with a matching sun, a camera framing
  the building, EEVEE with shadows and ray tracing, and the viewport switched to rendered shading

Pascal is a tool that produces files you own. The GLB it exports is plain glTF with a small,
documented set of `extras`; this add-on just reads them.

## Install

Blender 4.2 or newer.

1. Download `pascal-<version>.zip` from the [releases](https://github.com/pascalorg/blender-addon/releases)
   (or build one, see below).
2. Blender → Edit → Preferences → Get Extensions → ▾ → *Install from Disk…* → pick the zip.

## Use

- **File → Import → Pascal scene (.glb)**, or the **Pascal** tab in the 3D viewport sidebar (`N`).
- In Pascal: Settings → Export → *Export GLB* (the "Include in file" switches choose which
  procedural content is baked into the file).

Untick *Replace previous import* in the file dialog to keep an earlier import of the same project.
*Set up lighting* (file dialog, Pascal tab, and the add-on preferences for scenes sent from the
editor) adds a `Pascal sky` world, a `Pascal sun` and a `Pascal camera` — press Numpad 0 to look
through it. They are a starting point: tweak or delete them, a re-import reuses the same ones.

## What the file contains

Every Pascal scene node is a glTF node named by its `pascalId` with `extras`:

| extra | meaning |
|---|---|
| `pascalId` | stable id, also the node name |
| `kind` | `site`, `building`, `level`, `wall`, `door`, `window`, `item`, `zone`, `slab`, `ceiling`, `roof`, … |
| `label` | the user-facing name |
| `openable`, `clips` | doors and windows that actually bake an open clip (`"<id>: open"`, 1 s) |
| `polygon`, `color` | zones: `[x, z]` pairs (three.js Y-up) and a hex colour; the fill mesh is left out of the file |

Anything else in the file is ordinary glTF 2.0 that Blender's own importer handles.

## Development

```sh
# headless checks against the committed fixture (or any Pascal export)
./scripts/test.sh
./scripts/test.sh path/to/export.glb

# build the extension zip
/Applications/Blender.app/Contents/MacOS/Blender --command extension build --source-dir . --output-dir dist
```

Set `BLENDER` to point at another binary. Real exports for local testing go in
`tests/fixtures/local/` (git-ignored).

## Send to Blender (from the editor)

The add-on listens on `127.0.0.1:27412` (next few ports if taken) so the Pascal editor can hand
the live scene to the open Blender without a download step. Only web origins you allow can send:
`https://editor.pascal.app` by default; any other origin that tries shows up in the Pascal
sidebar tab with an *Allow* button (useful for a local editor on `http://localhost:3001`).
Port, origins and auto-start live in the add-on preferences.

Protocol, for anyone building another sender:

| Route | Purpose |
|---|---|
| `OPTIONS *` | CORS preflight, answers `Access-Control-Allow-Private-Network: true` (Chrome private-network access) |
| `GET /pascal/health` | `{ app: "blender", version, addon, port, allowed }` — `allowed` tells the caller whether its origin may send |
| `POST /pascal/import` | body `model/gltf-binary`; optional `X-Pascal-Project-Name` (percent-encoded UTF-8), `X-Pascal-Project-Id`, `X-Pascal-Version`; `202 { id, state: "queued" }`, `403 origin_not_allowed`, `400 not_glb` |
| `GET /pascal/import/<id>` | `{ id, state: queued | done | failed, summary?, error? }` |

Requests without an `Origin` header (curl, scripts on the same machine) are accepted: the
allowlist gates browsers, which are the only callers acting with ambient authority.

## Roadmap

- **Send to Blender** button in the Pascal editor (the listener side above is in).
- Pull a project straight from your Pascal account.

## License

GPL-3.0-or-later, as required for Blender add-ons.
