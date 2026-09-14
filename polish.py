"""Material polish after an import: say in Blender what glTF could not.

- Untextured see-through surfaces (windows, shower screens) arrive as an alpha
  blend and read as blue plastic film; they become Principled glass with
  transmission. Files that already carry transmission are left alone.
- Textures whose alpha channel actually cuts something out (leaf cards) get
  their alpha wired to the shader, which the glTF importer only does for
  materials the file marked as masked or blended.
- The site's own ground plane gets a calmer albedo so it stops blowing out
  the frame under a physical sun.
"""

from __future__ import annotations

from dataclasses import dataclass

import bpy
import numpy as np

GLASS_ALPHA_THRESHOLD = 0.6
GLASS_MAX_ROUGHNESS = 0.15
GROUND_COLOR = (0.30, 0.32, 0.29, 1.0)
GROUND_ROUGHNESS = 0.9
ALPHA_CUTOUT_THRESHOLD = 0.5


@dataclass
class PolishSummary:
    glass: int = 0
    cutouts: int = 0
    ground: int = 0

    def describe(self) -> str:
        parts = []
        if self.glass:
            parts.append(f"{self.glass} glass")
        if self.cutouts:
            parts.append(f"{self.cutouts} cutout")
        if self.ground:
            parts.append("ground")
        return ", ".join(parts)


def _principled(material: bpy.types.Material):
    if not material.use_nodes or material.node_tree is None:
        return None
    return next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


def _base_color_image(principled):
    link = next(iter(principled.inputs["Base Color"].links), None)
    node = link.from_node if link else None
    # The glTF importer may put a Mix (colour factor) between the image and the shader.
    while node is not None and node.type != "TEX_IMAGE":
        upstream = [l.from_node for s in node.inputs for l in s.links if l.from_node.type in {"TEX_IMAGE", "MIX", "MIX_RGB", "SEPARATE_COLOR"}]
        node = upstream[0] if upstream else None
    return node


def _has_cutout_alpha(image: bpy.types.Image, cache: dict[str, bool]) -> bool:
    if image.name in cache:
        return cache[image.name]
    result = False
    width, height = image.size
    if width and height and image.channels == 4:
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        result = bool((pixels[3::4] < ALPHA_CUTOUT_THRESHOLD).any())
    cache[image.name] = result
    return result


def polish_glass(material: bpy.types.Material) -> bool:
    principled = _principled(material)
    if principled is None:
        return False
    alpha_input = principled.inputs["Alpha"]
    if alpha_input.is_linked or alpha_input.default_value >= GLASS_ALPHA_THRESHOLD:
        return False
    if principled.inputs["Base Color"].is_linked:
        return False
    if principled.inputs["Transmission Weight"].default_value > 0:
        return False
    opacity = alpha_input.default_value
    color = principled.inputs["Base Color"].default_value
    principled.inputs["Base Color"].default_value = (
        *[c + (1.0 - c) * (1.0 - opacity) for c in color[:3]],
        1.0,
    )
    principled.inputs["Transmission Weight"].default_value = 1.0
    principled.inputs["Roughness"].default_value = min(
        principled.inputs["Roughness"].default_value, GLASS_MAX_ROUGHNESS
    )
    principled.inputs["IOR"].default_value = 1.5
    alpha_input.default_value = 1.0
    if hasattr(material, "use_raytrace_refraction"):
        material.use_raytrace_refraction = True
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    return True


def polish_cutout(material: bpy.types.Material, cache: dict[str, bool]) -> bool:
    principled = _principled(material)
    if principled is None or principled.inputs["Alpha"].is_linked:
        return False
    image_node = _base_color_image(principled)
    if image_node is None or image_node.image is None:
        return False
    if not _has_cutout_alpha(image_node.image, cache):
        return False
    material.node_tree.links.new(image_node.outputs["Alpha"], principled.inputs["Alpha"])
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    else:  # Blender < 4.2
        material.blend_method = "CLIP"
    material.use_backface_culling = False
    return True


def polish_ground(material: bpy.types.Material) -> bool:
    principled = _principled(material)
    if principled is None or principled.inputs["Base Color"].is_linked:
        return False
    if principled.inputs["Alpha"].default_value < 1.0:
        return False
    principled.inputs["Base Color"].default_value = GROUND_COLOR
    principled.inputs["Roughness"].default_value = GROUND_ROUGHNESS
    material.diffuse_color = GROUND_COLOR
    return True


def _kind_of(obj: bpy.types.Object) -> str | None:
    current = obj
    while current is not None:
        kind = current.get("kind")
        if kind:
            return str(kind)
        current = current.parent
    return None


def polish_materials(objects) -> PolishSummary:
    summary = PolishSummary()
    seen: set[str] = set()
    image_cache: dict[str, bool] = {}
    for obj in objects:
        if obj.type != "MESH":
            continue
        is_ground = _kind_of(obj) == "site"
        for slot in obj.material_slots:
            material = slot.material
            if material is None or material.name in seen:
                continue
            seen.add(material.name)
            if is_ground and polish_ground(material):
                summary.ground += 1
                continue
            if polish_glass(material):
                summary.glass += 1
            elif polish_cutout(material, image_cache):
                summary.cutouts += 1
    return summary
