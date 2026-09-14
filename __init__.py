"""Pascal for Blender.

Imports the GLB files Pascal produces and rebuilds their structure: one collection
per level and per kind, objects named after their labels, zone polygons, and the
door/window open clips as actions.
"""

from . import importer, ui


def register() -> None:
    importer.register()
    ui.register()


def unregister() -> None:
    ui.unregister()
    importer.unregister()
