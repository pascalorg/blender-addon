"""Headless checks for the importer.

    blender --background --factory-startup --python tests/run_tests.py -- [fixture.glb]
"""

import importlib.util
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "pascal-sample.glb")


def load_addon():
    spec = importlib.util.spec_from_file_location(
        "pascal_addon", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pascal_addon"] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def collection_names(collection):
    return sorted(child.name for child in collection.children)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    fixture = argv[0] if argv else DEFAULT_FIXTURE
    addon = load_addon()
    from pascal_addon import importer  # noqa: E402 — registered above

    engine_before = bpy.context.scene.render.engine
    summary = importer.import_pascal_file(bpy.context, fixture)
    print("SUMMARY", summary.describe())

    root = bpy.data.collections.get(importer.ROOT_COLLECTION)
    assert root is not None, "root Pascal collection missing"
    scene_collections = [c for c in root.children if c.get(importer.SITE_ID_PROPERTY)]
    assert len(scene_collections) == 1, f"expected one scene collection, got {len(scene_collections)}"
    scene_collection = scene_collections[0]
    assert summary.levels, "no levels found"
    for level in summary.levels:
        assert level in collection_names(scene_collection), f"level collection {level!r} missing"

    identity = [o for o in bpy.data.objects if o.get("pascalId") and o.get("kind") not in {"zone-floor"}]
    assert identity, "no identity objects"
    for obj in identity:
        kind = obj.get("kind")
        if kind in importer.STRUCTURE_KINDS:
            continue
        expected = importer._kind_collection_name(kind)
        homes = [c.name.rsplit(".", 1)[0] for c in obj.users_collection]
        assert homes == [expected], f"{obj.name} ({kind}) linked to {homes}, expected [{expected!r}]"
        assert obj.name.rsplit(".", 1)[0] == str(obj.get("label") or kind), f"{obj.name} not renamed to its label"

    zones = [o for o in bpy.data.objects if o.get("kind") == "zone"]
    fills = [o for o in bpy.data.objects if o.get("kind") == "zone-floor"]
    assert len(fills) == len([z for z in zones if z.get("polygon") is not None]), "zone fill count"
    for fill in fills:
        assert fill.parent in zones and len(fill.data.polygons) == 1 and fill.data.materials
    assert summary.zones == len(fills)

    doors = [o for o in bpy.data.objects if o.get("kind") == "door" and o.get("openable")]
    if doors:
        assert summary.actions >= len(doors), f"{summary.actions} actions for {len(doors)} openable doors"
        for door in doors:
            clips = list(door.get("clips") or [])
            assert clips, f"{door.name} has no clips"
            animated = [d for d in door.children_recursive if d.animation_data and d.animation_data.nla_tracks]
            assert animated, f"{door.name} has no animated part"
            track_names = {t.name for d in animated for t in d.animation_data.nla_tracks}
            assert set(clips) <= track_names, f"{door.name} clips {clips} not among tracks {track_names}"

    units = bpy.context.scene.unit_settings
    assert (units.system, units.length_unit) == ("METRIC", "METERS")

    assert summary.lighting, "lighting was not set up"
    world = bpy.context.scene.world
    assert world is not None and any(n.type == "TEX_SKY" for n in world.node_tree.nodes), "no sky"
    sun = bpy.data.objects.get(importer.lighting.SUN_NAME)
    assert sun is not None and sun.type == "LIGHT" and sun.data.type == "SUN"
    camera = bpy.data.objects.get(importer.lighting.CAMERA_NAME)
    assert camera is not None and bpy.context.scene.camera is camera
    ground = bpy.data.objects.get(importer.lighting.GROUND_NAME)
    assert ground is not None and ground.type == "MESH" and ground.data.materials
    assert bpy.context.scene.render.engine == engine_before, "lighting changed the render engine"

    objects_before = len(bpy.data.objects)
    second = importer.import_pascal_file(bpy.context, fixture)
    assert second.replaced, "second import did not replace the first"
    assert len(bpy.data.objects) == objects_before, "re-import changed the object count"
    assert len([c for c in root.children if c.get(importer.SITE_ID_PROPERTY)]) == 1
    assert len([o for o in bpy.data.objects if o.name.startswith(importer.lighting.SUN_NAME)]) == 1
    assert len([o for o in bpy.data.objects if o.name.startswith(importer.lighting.CAMERA_NAME)]) == 1
    assert len([o for o in bpy.data.objects if o.name.startswith(importer.lighting.GROUND_NAME)]) == 1

    plain = importer.import_pascal_file(bpy.context, fixture, scene_name="plain", setup_lighting=False)
    assert not plain.lighting

    check_polish()
    print("OK", summary.describe())


def check_polish() -> None:
    from pascal_addon import polish

    glass = bpy.data.materials.new("t-glass")
    glass.use_nodes = True
    node = next(n for n in glass.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    node.inputs["Base Color"].default_value = (0.2, 0.6, 0.9, 1.0)
    node.inputs["Alpha"].default_value = 0.3
    node.inputs["Roughness"].default_value = 0.5
    assert polish.polish_glass(glass)
    assert node.inputs["Transmission Weight"].default_value == 1.0
    assert node.inputs["Alpha"].default_value == 1.0
    assert node.inputs["Roughness"].default_value <= polish.GLASS_MAX_ROUGHNESS + 1e-6
    assert abs(node.inputs["Base Color"].default_value[0] - (0.2 + 0.8 * 0.7)) < 1e-5
    assert not polish.polish_glass(glass), "glass polished twice"

    plastic = bpy.data.materials.new("t-plastic")
    plastic.use_nodes = True
    next(n for n in plastic.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Alpha"].default_value = 0.8
    assert not polish.polish_glass(plastic)

    leaf_image = bpy.data.images.new("t-leaf", 4, 4, alpha=True)
    pixels = [1.0, 1.0, 1.0, 1.0] * 16
    pixels[3] = 0.0
    leaf_image.pixels = pixels
    leaf = bpy.data.materials.new("t-leaf")
    leaf.use_nodes = True
    principled = next(n for n in leaf.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    tex = leaf.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = leaf_image
    leaf.node_tree.links.new(tex.outputs["Color"], principled.inputs["Base Color"])
    assert polish.polish_cutout(leaf, {})
    assert principled.inputs["Alpha"].is_linked

    solid_image = bpy.data.images.new("t-solid", 4, 4, alpha=True)
    solid_image.pixels = [1.0] * 64
    solid = bpy.data.materials.new("t-solid")
    solid.use_nodes = True
    principled = next(n for n in solid.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    tex = solid.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = solid_image
    solid.node_tree.links.new(tex.outputs["Color"], principled.inputs["Base Color"])
    assert not polish.polish_cutout(solid, {}), "opaque texture wrongly cut out"

    site = bpy.data.objects.new("t-site", bpy.data.meshes.new("t-site"))
    site["kind"] = "site"
    ground = bpy.data.materials.new("t-ground")
    ground.use_nodes = True
    site.data.materials.append(ground)
    result = polish.polish_materials([site])
    assert result.ground == 1 and result.describe() == "ground", result


if __name__ == "__main__":
    main()
