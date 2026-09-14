"""Rebuild a Pascal scene's structure after Blender's glTF importer has loaded it.

Pascal stamps every scene node with glTF extras (`pascalId`, `kind`, `label`, and for
zones `polygon` + `color`). Blender's importer keeps those as custom properties and
turns the `"<id>: open"` clips into NLA tracks, so all the structure we need is
already in the file; this module only sorts it into collections, names things, and
draws the zones that the export deliberately leaves as outlines.
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

ROOT_COLLECTION = "Pascal"
SITE_ID_PROPERTY = "pascal_site_id"
STRUCTURE_KINDS = {"site", "building", "level"}
KIND_COLLECTIONS = {
    "wall": "Walls",
    "door": "Doors",
    "window": "Windows",
    "item": "Items",
    "zone": "Zones",
    "slab": "Slabs",
    "ceiling": "Ceilings",
    "roof": "Roofs",
    "fence": "Fences",
    "spawn": "Spawn points",
    "shelf": "Shelves",
    "stair": "Stairs",
    "column": "Columns",
    "beam": "Beams",
}
ZONE_ALPHA = 0.35
ZONE_LIFT = 0.005


@dataclass
class ImportSummary:
    name: str
    site_id: str | None
    levels: list[str] = field(default_factory=list)
    objects: int = 0
    zones: int = 0
    actions: int = 0
    replaced: bool = False

    def describe(self) -> str:
        parts = [f"{self.objects} objects", f"{len(self.levels)} levels"]
        if self.zones:
            parts.append(f"{self.zones} zones")
        if self.actions:
            parts.append(f"{self.actions} door/window actions")
        verb = "Replaced" if self.replaced else "Imported"
        return f"{verb} {self.name}: " + ", ".join(parts)


# --- reading the file -------------------------------------------------------


def _read_gltf_json(filepath: str) -> dict:
    with open(filepath, "rb") as handle:
        head = handle.read(12)
        if len(head) == 12 and head[:4] == b"glTF":
            chunk_length, chunk_type = struct.unpack("<II", handle.read(8))
            if chunk_type != 0x4E4F534A:  # "JSON"
                raise ValueError("GLB file has no JSON chunk")
            return json.loads(handle.read(chunk_length))
        handle.seek(0)
        return json.loads(handle.read())


def peek_site_id(filepath: str) -> str | None:
    """The site's pascalId, read from the file header so a re-import can replace
    the previous one before Blender allocates `.001` names."""
    try:
        gltf = _read_gltf_json(filepath)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    for node in gltf.get("nodes", []):
        extras = node.get("extras") or {}
        if extras.get("kind") == "site" and extras.get("pascalId"):
            return str(extras["pascalId"])
    return None


# --- collections ------------------------------------------------------------


def _root_collection(context) -> bpy.types.Collection:
    root = bpy.data.collections.get(ROOT_COLLECTION)
    if root is None:
        root = bpy.data.collections.new(ROOT_COLLECTION)
    if root.name not in context.scene.collection.children:
        context.scene.collection.children.link(root)
    return root


def _child_collection(parent: bpy.types.Collection, name: str) -> bpy.types.Collection:
    for child in parent.children:
        if child.name == name or child.name.rsplit(".", 1)[0] == name:
            return child
    child = bpy.data.collections.new(name)
    parent.children.link(child)
    return child


def _collection_tree(collection: bpy.types.Collection):
    yield collection
    for child in collection.children:
        yield from _collection_tree(child)


def remove_previous_import(context, site_id: str) -> bool:
    """Delete the collection tree (and its objects) of an earlier import of `site_id`."""
    root = bpy.data.collections.get(ROOT_COLLECTION)
    if root is None:
        return False
    previous = [c for c in root.children if c.get(SITE_ID_PROPERTY) == site_id]
    if not previous:
        return False
    for scene_collection in previous:
        tree = list(_collection_tree(scene_collection))
        objects = {obj for collection in tree for obj in collection.objects}
        bpy.data.batch_remove(objects)
        bpy.data.batch_remove(reversed(tree))
    bpy.ops.outliner.orphans_purge(do_recursive=True) if bpy.ops.outliner.orphans_purge.poll() else None
    return True


def _link_only(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
    for current in list(obj.users_collection):
        if current != collection:
            current.objects.unlink(obj)
    if obj.name not in collection.objects:
        collection.objects.link(obj)


# --- identity ---------------------------------------------------------------


def _kind(obj: bpy.types.Object) -> str | None:
    kind = obj.get("kind")
    return str(kind) if kind else None


def _label(obj: bpy.types.Object) -> str | None:
    label = obj.get("label")
    return str(label) if label else None


def _identity_ancestor(obj: bpy.types.Object) -> bpy.types.Object | None:
    current = obj
    while current is not None:
        if current.get("pascalId"):
            return current
        current = current.parent
    return None


def _level_ancestor(obj: bpy.types.Object) -> bpy.types.Object | None:
    current = obj.parent
    while current is not None:
        if _kind(current) == "level":
            return current
        current = current.parent
    return None


def _kind_collection_name(kind: str) -> str:
    return KIND_COLLECTIONS.get(kind, kind.replace("_", " ").capitalize() + "s")


# --- zones ------------------------------------------------------------------


def _hex_to_rgba(value: str, alpha: float) -> tuple[float, float, float, float]:
    text = value.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        r, g, b = (int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        r = g = b = 0.5
    # Custom props hold sRGB hex; Blender's base color is scene-linear.
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)) + (alpha,)


def _zone_material(color: str) -> bpy.types.Material:
    name = f"Pascal zone {color}"
    material = bpy.data.materials.get(name)
    if material is not None:
        return material
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    rgba = _hex_to_rgba(color, ZONE_ALPHA)
    material.diffuse_color = rgba
    principled = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is not None:
        principled.inputs["Base Color"].default_value = rgba
        principled.inputs["Alpha"].default_value = ZONE_ALPHA
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    else:  # Blender < 4.2
        material.blend_method = "BLEND"
    return material


def build_zone_mesh(zone: bpy.types.Object, collection: bpy.types.Collection) -> bpy.types.Object | None:
    """Pascal strips the zone fill mesh from the export and leaves the polygon in
    extras (`[x, z]` pairs, three.js Y-up). Rebuild it as a flat face in the zone's
    local space, which the glTF importer has already converted to Z-up."""
    polygon = zone.get("polygon")
    if polygon is None or len(polygon) < 3:
        return None
    points = [(float(p[0]), -float(p[1]), ZONE_LIFT) for p in polygon]
    mesh = bpy.data.meshes.new(f"{zone.name} zone")
    mesh.from_pydata(points, [], [list(range(len(points)))])
    mesh.update()
    color = str(zone.get("color") or "#888888")
    mesh.materials.append(_zone_material(color))
    fill = bpy.data.objects.new(f"{zone.name} floor", mesh)
    fill.parent = zone
    fill.hide_render = True
    fill["pascalId"] = zone.get("pascalId")
    fill["kind"] = "zone-floor"
    collection.objects.link(fill)
    return fill


# --- the import -------------------------------------------------------------


def import_pascal_file(
    context, filepath: str, *, scene_name: str | None = None, replace: bool = True
) -> ImportSummary:
    site_id = peek_site_id(filepath)
    replaced = bool(replace and site_id and remove_previous_import(context, site_id))

    before = {obj.as_pointer() for obj in bpy.data.objects}
    actions_before = {action.as_pointer() for action in bpy.data.actions}
    bpy.ops.import_scene.gltf(
        filepath=filepath,
        import_scene_extras=True,
        import_shading="NORMALS",
        merge_vertices=False,
        import_select_created_objects=False,
    )
    created = [obj for obj in bpy.data.objects if obj.as_pointer() not in before]
    new_actions = [a for a in bpy.data.actions if a.as_pointer() not in actions_before]

    name = scene_name or os.path.splitext(os.path.basename(filepath))[0]
    root = _root_collection(context)
    scene_collection = _child_collection(root, name)
    if site_id:
        scene_collection[SITE_ID_PROPERTY] = site_id

    level_collections: dict[int, bpy.types.Collection] = {}
    levels = [obj for obj in created if _kind(obj) == "level"]
    for level in levels:
        level_collections[level.as_pointer()] = _child_collection(
            scene_collection, _label(level) or level.name
        )

    summary = ImportSummary(name=name, site_id=site_id, replaced=replaced)
    summary.levels = [_label(level) or level.name for level in levels]

    for obj in created:
        identity = _identity_ancestor(obj)
        kind = _kind(identity) if identity else None
        level = _level_ancestor(identity if identity else obj)
        home = level_collections.get(level.as_pointer()) if level else scene_collection
        if identity is obj and identity.get("label") and kind not in STRUCTURE_KINDS:
            obj.name = str(identity["label"])
        if kind is None:
            # The export's own root (`scene-renderer`) and any stray unstamped node.
            _link_only(obj, home if obj.parent is None else _child_collection(home, "Other"))
        elif kind == "level":
            _link_only(obj, level_collections[identity.as_pointer()])
        elif kind in STRUCTURE_KINDS:
            _link_only(obj, scene_collection)
        else:
            _link_only(obj, _child_collection(home, _kind_collection_name(kind)))

    for zone in [obj for obj in created if _kind(obj) == "zone"]:
        level = _level_ancestor(zone)
        home = level_collections.get(level.as_pointer()) if level else scene_collection
        if build_zone_mesh(zone, _child_collection(home, _kind_collection_name("zone"))):
            summary.zones += 1

    summary.objects = len(created)
    summary.actions = len(new_actions)

    units = context.scene.unit_settings
    units.system = "METRIC"
    units.length_unit = "METERS"
    units.scale_length = 1.0
    return summary


class PASCAL_OT_import_glb(bpy.types.Operator, ImportHelper):
    """Import a Pascal scene (GLB) with its levels, zones, doors and metadata"""

    bl_idname = "pascal.import_glb"
    bl_label = "Pascal scene (.glb)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".glb"
    filter_glob: StringProperty(default="*.glb;*.gltf", options={"HIDDEN"})
    replace: BoolProperty(
        name="Replace previous import",
        description="Replace an earlier import of the same Pascal project instead of adding a copy",
        default=True,
    )

    def execute(self, context):
        try:
            summary = import_pascal_file(context, self.filepath, replace=self.replace)
        except Exception as error:  # noqa: BLE001 — surface anything the glTF importer raised
            self.report({"ERROR"}, f"Pascal import failed: {error}")
            return {"CANCELLED"}
        context.window_manager.pascal_last_import = summary.describe()
        self.report({"INFO"}, summary.describe())
        _frame_all(context)
        return {"FINISHED"}


def _frame_all(context) -> None:
    for area in getattr(context.screen, "areas", []):
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None:
            continue
        with context.temp_override(area=area, region=region):
            if bpy.ops.view3d.view_all.poll():
                bpy.ops.view3d.view_all()
        return


def register() -> None:
    bpy.utils.register_class(PASCAL_OT_import_glb)
    bpy.types.WindowManager.pascal_last_import = StringProperty(default="")


def unregister() -> None:
    del bpy.types.WindowManager.pascal_last_import
    bpy.utils.unregister_class(PASCAL_OT_import_glb)
