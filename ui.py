"""File menu entry and the Pascal sidebar tab."""

import bpy

from .importer import PASCAL_OT_import_glb


def _menu_import(self, context) -> None:
    self.layout.operator(PASCAL_OT_import_glb.bl_idname, text="Pascal scene (.glb)")


class VIEW3D_PT_pascal(bpy.types.Panel):
    bl_label = "Pascal"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pascal"

    def draw(self, context) -> None:
        layout = self.layout
        layout.operator(PASCAL_OT_import_glb.bl_idname, text="Import Pascal scene", icon="IMPORT")
        last = context.window_manager.pascal_last_import
        if last:
            box = layout.box()
            for line in _wrap(last, 34):
                box.label(text=line)


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


def register() -> None:
    bpy.utils.register_class(VIEW3D_PT_pascal)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.utils.unregister_class(VIEW3D_PT_pascal)
