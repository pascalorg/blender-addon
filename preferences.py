"""Add-on preferences: listener port, allowed web origins, auto-start."""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import lighting, listener


def listener_presets():
    return lighting.PRESET_ITEMS


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
    polish_materials: BoolProperty(
        name="Polish materials on import",
        description="Turn see-through surfaces into glass, wire cutout alpha on leaf textures, calm the site ground",
        default=True,
    )
    lighting_preset: EnumProperty(
        name="Lighting",
        description="Look applied by Set up lighting",
        items=listener_presets(),
        default=lighting.DEFAULT_PRESET,
    )
    render_engine: EnumProperty(
        name="Render engine",
        description="Whether Set up lighting switches the render engine",
        items=lighting.ENGINE_CHOICES,
        default="KEEP",
    )
    setup_lighting: BoolProperty(
        name="Set up lighting on import",
        description="Add a sky and sun, frame a camera, and switch the viewport to rendered shading after each import",
        default=True,
    )

    def draw(self, context) -> None:
        layout = self.layout
        layout.prop(self, "polish_materials")
        layout.prop(self, "setup_lighting")
        layout.prop(self, "lighting_preset")
        layout.prop(self, "render_engine")
        layout.prop(self, "auto_start")
        layout.prop(self, "port")
        layout.prop(self, "allowed_origins")
        layout.label(text="Origins that call Blender while not allowed show up in the Pascal sidebar tab with an Allow button.")


def register() -> None:
    bpy.utils.register_class(PascalPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(PascalPreferences)
