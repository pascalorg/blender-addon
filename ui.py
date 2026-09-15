"""File menu entry, the Pascal sidebar tab, and the listener operators."""

import bpy
from bpy.props import StringProperty

from . import importer, lighting, listener
from .importer import PASCAL_OT_import_glb


def _menu_import(self, context) -> None:
    self.layout.operator(PASCAL_OT_import_glb.bl_idname, text="Pascal scene (.glb)")


class PASCAL_OT_listener_start(bpy.types.Operator):
    """Start listening for scenes sent from the Pascal editor"""

    bl_idname = "pascal.listener_start"
    bl_label = "Start listening"

    def execute(self, context):
        try:
            port = listener.start()
        except OSError as error:
            self.report({"ERROR"}, f"Could not start the Pascal listener: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Pascal listener on 127.0.0.1:{port}")
        return {"FINISHED"}


class PASCAL_OT_listener_stop(bpy.types.Operator):
    """Stop listening for scenes sent from the Pascal editor"""

    bl_idname = "pascal.listener_stop"
    bl_label = "Stop listening"

    def execute(self, context):
        listener.stop()
        return {"FINISHED"}


class PASCAL_OT_allow_origin(bpy.types.Operator):
    """Allow this web origin to send scenes to Blender"""

    bl_idname = "pascal.allow_origin"
    bl_label = "Allow origin"

    origin: StringProperty()

    def execute(self, context):
        listener.allow_origin(self.origin)
        self.report({"INFO"}, f"{self.origin} can now send scenes to Blender")
        return {"FINISHED"}


class PASCAL_OT_ignore_origin(bpy.types.Operator):
    """Dismiss this origin's request"""

    bl_idname = "pascal.ignore_origin"
    bl_label = "Ignore"

    origin: StringProperty()

    def execute(self, context):
        listener.forget_pending(self.origin)
        return {"FINISHED"}


class PASCAL_OT_relight(bpy.types.Operator):
    """Rebuild the Pascal sky, sun and camera with the selected lighting preset"""

    bl_idname = "pascal.relight"
    bl_label = "Apply lighting preset"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = bpy.data.collections.get(importer.ROOT_COLLECTION)
        if root is None:
            self.report({"WARNING"}, "Import a Pascal scene first")
            return {"CANCELLED"}
        objects = [obj for collection in importer._collection_tree(root) for obj in collection.objects]
        lighting.setup_lighting(context, root, objects)
        return {"FINISHED"}


class VIEW3D_PT_pascal(bpy.types.Panel):
    bl_label = "Pascal"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pascal"

    def draw(self, context) -> None:
        layout = self.layout
        layout.operator(PASCAL_OT_import_glb.bl_idname, text="Import Pascal scene", icon="IMPORT")
        prefs = _prefs(context)
        if prefs is not None:
            layout.prop(prefs, "polish_materials")
            layout.prop(prefs, "setup_lighting")
            row = layout.row()
            row.enabled = prefs.setup_lighting
            row.prop(prefs, "lighting_preset", text="")
            row.operator(PASCAL_OT_relight.bl_idname, text="", icon="FILE_REFRESH")

        box = layout.box()
        if listener.running():
            box.label(text=f"Listening on 127.0.0.1:{listener.port()}", icon="LINKED")
            box.operator(PASCAL_OT_listener_stop.bl_idname, text="Stop", icon="PAUSE")
        else:
            box.label(text="Not listening", icon="UNLINKED")
            box.operator(PASCAL_OT_listener_start.bl_idname, text="Listen for the Pascal editor", icon="PLAY")
            if listener.STATE.last_error:
                for line in _wrap(listener.STATE.last_error, 34):
                    box.label(text=line, icon="ERROR")

        pending = listener.pending_origins()
        if pending:
            box = layout.box()
            box.label(text="Wants to send scenes:", icon="QUESTION")
            for origin in pending:
                row = box.row(align=True)
                row.label(text=origin)
                row.operator(PASCAL_OT_allow_origin.bl_idname, text="Allow").origin = origin
                row.operator(PASCAL_OT_ignore_origin.bl_idname, text="", icon="X").origin = origin

        last = context.window_manager.pascal_last_import
        if last:
            box = layout.box()
            for line in _wrap(last, 34):
                box.label(text=line)


def _prefs(context):
    try:
        return context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


CLASSES = (
    PASCAL_OT_relight,
    PASCAL_OT_listener_start,
    PASCAL_OT_listener_stop,
    PASCAL_OT_allow_origin,
    PASCAL_OT_ignore_origin,
    VIEW3D_PT_pascal,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
