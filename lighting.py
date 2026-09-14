"""Optional "wow" lighting after an import: a physical sky with a matching sun, a
camera framing the building, EEVEE with shadows and ray tracing, AgX, and the
viewport switched to rendered shading. Everything is named so a re-import
updates the same world, sun and camera instead of adding more."""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

WORLD_NAME = "Pascal sky"
SUN_NAME = "Pascal sun"
CAMERA_NAME = "Pascal camera"
SUN_ELEVATION = math.radians(32)
SUN_ROTATION = math.radians(135)  # side light: facades get a gradient, the visible sky stays bright
SUN_ENERGY = 4.5
SUN_COLOR = (1.0, 0.93, 0.82)
SKY_STRENGTH = 0.4
GROUND_NAME = "Pascal ground"
GROUND_COLOR = (0.22, 0.24, 0.23)
GROUND_RADIUS = 2000.0
EXPOSURE = -0.5
CAMERA_LENS = 32.0
CAMERA_AZIMUTH = math.radians(-35)
CAMERA_ELEVATION = math.radians(20)
ENGINE_CANDIDATES = ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT")


def _world(scene: bpy.types.Scene) -> bpy.types.World:
    world = bpy.data.worlds.get(WORLD_NAME) or bpy.data.worlds.new(WORLD_NAME)
    scene.world = world
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()
    sky = nodes.new("ShaderNodeTexSky")
    sky.location = (-500, 0)
    for sky_type in ("MULTIPLE_SCATTERING", "NISHITA"):
        try:
            sky.sky_type = sky_type
            break
        except TypeError:
            continue
    for name, value in (
        ("sun_elevation", SUN_ELEVATION),
        ("sun_rotation", SUN_ROTATION),
        ("sun_intensity", 1.0),
        ("sun_disc", False),  # the sun lamp below provides the direct light
        ("altitude", 50.0),
        ("air_density", 1.0),
        ("dust_density", 2.0),
        ("turbidity", 3.5),
        ("ozone_density", 1.0),
    ):
        if hasattr(sky, name):
            setattr(sky, name, value)
    background = nodes.new("ShaderNodeBackground")
    background.location = (-100, 0)
    background.inputs["Strength"].default_value = SKY_STRENGTH
    output = nodes.new("ShaderNodeOutputWorld")
    output.location = (150, 0)
    links.new(sky.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    return world


def _sun(root: bpy.types.Collection) -> bpy.types.Object:
    sun = bpy.data.objects.get(SUN_NAME)
    if sun is None or sun.type != "LIGHT":
        light = bpy.data.lights.new(SUN_NAME, "SUN")
        sun = bpy.data.objects.new(SUN_NAME, light)
        root.objects.link(sun)
    light = sun.data
    light.energy = SUN_ENERGY
    light.color = SUN_COLOR
    light.angle = math.radians(0.6)
    light.use_shadow = True
    # A sun lamp shines down its local -Z: tilt it from the zenith by the
    # complement of the elevation, then spin it to the sky's sun rotation.
    sun.rotation_euler = (math.pi / 2 - SUN_ELEVATION, 0.0, SUN_ROTATION)
    sun.location = (0.0, 0.0, 10.0)
    return sun


def _ground(root: bpy.types.Collection, bounds: tuple[Vector, Vector] | None) -> bpy.types.Object:
    """A wide neutral disc under the site: the physical sky is dark below the
    horizon and would otherwise show past the edge of the exported ground."""
    ground = bpy.data.objects.get(GROUND_NAME)
    if ground is None or ground.type != "MESH":
        mesh = bpy.data.meshes.new(GROUND_NAME)
        segments = 64
        points = [
            (GROUND_RADIUS * math.cos(2 * math.pi * i / segments), GROUND_RADIUS * math.sin(2 * math.pi * i / segments), 0.0)
            for i in range(segments)
        ]
        mesh.from_pydata(points, [], [list(range(segments))])
        mesh.update()
        ground = bpy.data.objects.new(GROUND_NAME, mesh)
        root.objects.link(ground)
    material = bpy.data.materials.get(GROUND_NAME)
    if material is None:
        material = bpy.data.materials.new(GROUND_NAME)
        material.use_nodes = True
        material.diffuse_color = (*GROUND_COLOR, 1.0)
        principled = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if principled is not None:
            principled.inputs["Base Color"].default_value = (*GROUND_COLOR, 1.0)
            principled.inputs["Roughness"].default_value = 1.0
    if not ground.data.materials:
        ground.data.materials.append(material)
    floor = bounds[0].z if bounds else 0.0
    ground.location = (0.0, 0.0, floor - 0.01)
    ground.hide_select = True
    return ground


def _bounds(objects) -> tuple[Vector, Vector] | None:
    low = Vector((math.inf,) * 3)
    high = Vector((-math.inf,) * 3)
    found = False
    for obj in objects:
        if obj.type != "MESH" or obj.hide_render or obj.get("kind") == "zone-floor":
            continue
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            low = Vector(map(min, low, point))
            high = Vector(map(max, high, point))
            found = True
    return (low, high) if found else None


def _camera(scene: bpy.types.Scene, root: bpy.types.Collection, objects) -> bpy.types.Object | None:
    bounds = _bounds(objects)
    if bounds is None:
        return None
    low, high = bounds
    center = (low + high) / 2
    size = max(high - low)
    camera = bpy.data.objects.get(CAMERA_NAME)
    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        root.objects.link(camera)
    camera.data.lens = CAMERA_LENS
    camera.data.clip_end = max(1000.0, size * 20)
    fov = 2 * math.atan(18.0 / CAMERA_LENS)  # 36 mm sensor width
    distance = (size * 0.62) / math.tan(fov / 2)
    direction = Vector(
        (
            math.cos(CAMERA_ELEVATION) * math.sin(CAMERA_AZIMUTH),
            -math.cos(CAMERA_ELEVATION) * math.cos(CAMERA_AZIMUTH),
            math.sin(CAMERA_ELEVATION),
        )
    )
    target = Vector((center.x, center.y, low.z + (high.z - low.z) * 0.4))
    camera.location = target + direction * distance
    camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    return camera


def _render_settings(scene: bpy.types.Scene) -> None:
    for engine in ENGINE_CANDIDATES:
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    eevee = scene.eevee
    for name, value in (
        ("use_shadows", True),
        ("use_raytracing", True),
        ("use_fast_gi", True),
        ("taa_render_samples", 64),
        ("shadow_ray_count", 2),
    ):
        if hasattr(eevee, name):
            setattr(eevee, name, value)
    view = scene.view_settings
    for name, value in (("view_transform", "AgX"), ("look", "AgX - Medium High Contrast")):
        try:
            setattr(view, name, value)
        except TypeError:
            pass
    view.exposure = EXPOSURE
    if scene.world is not None and hasattr(scene.world, "use_sun_shadow"):
        scene.world.use_sun_shadow = False


def _rendered_viewports() -> None:
    if bpy.app.background:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.shading.type = "RENDERED"
                    space.shading.use_scene_lights_render = True
                    space.shading.use_scene_world_render = True
            area.tag_redraw()


def setup_lighting(context, root: bpy.types.Collection, objects) -> None:
    scene = context.scene
    _world(scene)
    _sun(root)
    _ground(root, _bounds(objects))
    _camera(scene, root, objects)
    _render_settings(scene)
    _rendered_viewports()
