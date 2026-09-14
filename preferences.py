"""Add-on preferences: listener port, allowed web origins, auto-start."""

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from . import listener


class PascalPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    port: IntProperty(
        name="Listener port",
        description="Loopback port the Pascal editor sends scenes to (falls back to the next few ports when taken)",
        default=listener.DEFAULT_PORT,
        min=1024,
        max=65535,
    )
    allowed_origins: StringProperty(
        name="Allowed origins",
        description="Comma-separated web origins allowed to send scenes to Blender",
        default=", ".join(listener.DEFAULT_ALLOWED_ORIGINS),
    )
    auto_start: BoolProperty(
        name="Listen for the Pascal editor on startup",
        default=True,
    )

    def draw(self, context) -> None:
        layout = self.layout
        layout.prop(self, "auto_start")
        layout.prop(self, "port")
        layout.prop(self, "allowed_origins")
        layout.label(text="Origins that call Blender while not allowed show up in the Pascal sidebar tab with an Allow button.")


def register() -> None:
    bpy.utils.register_class(PascalPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(PascalPreferences)
