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

## Roadmap

- **Send to Blender**: a loopback listener in the add-on and a button in the Pascal editor, so the
  live scene lands in an open Blender without a download step.
- Pull a project straight from your Pascal account.

## License

GPL-3.0-or-later, as required for Blender add-ons.
