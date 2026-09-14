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

    objects_before = len(bpy.data.objects)
    second = importer.import_pascal_file(bpy.context, fixture)
    assert second.replaced, "second import did not replace the first"
    assert len(bpy.data.objects) == objects_before, "re-import changed the object count"
    assert len([c for c in root.children if c.get(importer.SITE_ID_PROPERTY)]) == 1
    print("OK", summary.describe())


if __name__ == "__main__":
    main()
