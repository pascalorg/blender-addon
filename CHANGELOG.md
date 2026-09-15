# Changelog

## 0.6.0

Review pass for extensions.blender.org. Nothing changes in what the add-on does; the
listener that the editor's *Send to Blender* talks to is now built differently.

- **The listener no longer runs inside Blender.** `bpy` is main-thread only, so hosting an
  HTTP server on a background thread was a crash waiting to happen. `server.py` now runs as a
  helper process on Blender's bundled Python and owns the socket; it spools each received
  scene into a temporary folder and a `bpy.app.timers` callback imports it on Blender's main
  thread. No `threading`, no `queue`.
- **The helper always goes away.** It stops with Blender, and if Blender ever quits without
  stopping it, a stale heartbeat makes it exit on its own and take its temporary folder with
  it. No orphan process, no leftover files.
- **The package is built from an allowlist.** `[build] paths` in the manifest names the files
  that make up the extension, so nothing else in the source tree can end up in the zip. CI
  builds with `blender --command extension build`, validates the result, and fails if the
  package contains an archive.
- Listener failures now say why, in the Pascal sidebar tab.
- The port preference explains that the editor looks at that port and the four after it.

Unchanged: the HTTP contract (`/pascal/health`, `/pascal/import`), the origin allowlist and
its *Allow* button, importing, material polish, and the lighting presets.

## 0.5.0

First public build (Blender 4.2+).

- Import Pascal GLBs as level and kind collections, labelled objects, zone floors, door and
  window clips as actions
- Send to Blender: loopback listener the Pascal editor hands the live scene to, with an origin
  allowlist and an *Allow* button
- Material polish: glass with transmission, leaf cutout alpha, calmer site ground
- Lighting presets on Blender's bundled HDRIs: Daylight, Golden hour, Overcast, Night (room and
  porch lights), Physical sky
