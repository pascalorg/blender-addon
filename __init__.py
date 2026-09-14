"""Pascal for Blender.

Imports the GLB files Pascal produces and rebuilds their structure: one collection
per level and per kind, objects named after their labels, zone polygons, and the
door/window open clips as actions. A loopback listener lets the Pascal editor send
a scene straight into the open Blender.
"""

from . import importer, listener, preferences, ui


def register() -> None:
    importer.register()
    preferences.register()
    listener.register()
    ui.register()


def unregister() -> None:
    ui.unregister()
    listener.unregister()
    preferences.unregister()
    importer.unregister()
