"""Read-only scene snapshot and matched source/texture diagnostic renders in Blender.

Launch with --background --factory-startup --disable-autoexec <scene.blend>
--python this_file -- --output <fresh directory>. Never saves the input .blend.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--target", default="")
parser.add_argument("--source", default="", help="Explicit source for meshes whose workflow did not retain the link")
parser.add_argument("--size", type=int, default=640)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
output = args.output.resolve()
if output.exists() and any(output.iterdir()):
    raise ValueError("Diagnostic output must be empty")
output.mkdir(parents=True, exist_ok=True)

name = "_remi_texture_diagnostic"
spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules[name] = addon
spec.loader.exec_module(addon)
adapter = __import__(name + ".blender.splat_projection", fromlist=["export_mesh_archive"])
splats = __import__(name + ".blender.gaussian_splats", fromlist=["splat_arrays"])
sys.path.insert(0, str(ROOT / "integrations/splat_mesh"))
from remi_surface.geometry import look_at

targets = [obj for obj in bpy.data.objects if obj.type == "MESH" and obj.get("remi_texture_report")]
target = bpy.data.objects.get(args.target) if args.target else (targets[0] if len(targets) == 1 else None)
if target is None:
    raise ValueError("Choose one baked target with --target")
target_scene = next(scene for scene in bpy.data.scenes if target.name in scene.objects)
if bpy.context.window is not None:
    bpy.context.window.scene = target_scene
source, archive = adapter.resolve_splat_source(target)
if args.source:
    source = bpy.data.objects.get(args.source)
if source is None or not splats.uses_native_splats(source):
    raise ValueError("Diagnostic requires the target's linked native Gaussian source")
if source.name not in target_scene.objects:
    target_scene.collection.objects.link(source)
bpy.context.view_layer.objects.active = target
target.select_set(True)
adapter.export_mesh_archive(bpy.context, target, output / "mesh.npz")
adapter.write_world_splat_archive(source, output / "source.npz")
with np.load(output / "source.npz", allow_pickle=False) as data:
    points = data["positions"]
    low, high = points.min(0), points.max(0)
center = (low + high) * 0.5
radius = float(np.linalg.norm(high - low) * 0.5)
old_albedo = str(target.get("remi_texture_albedo", ""))
if not Path(old_albedo).is_file():
    raise ValueError("Old albedo PNG is needed for this diagnostic")
image = bpy.data.images.load(old_albedo, check_existing=False)
image.colorspace_settings.name = "sRGB"
old_materials = list(target.data.materials)
material = bpy.data.materials.new("Texture comparison · emission")
material.use_nodes = True
nodes, links = material.node_tree.nodes, material.node_tree.links
nodes.clear()
texture = nodes.new("ShaderNodeTexImage")
texture.image = image
emission = nodes.new("ShaderNodeEmission")
out = nodes.new("ShaderNodeOutputMaterial")
links.new(texture.outputs["Color"], emission.inputs["Color"])
links.new(emission.outputs["Emission"], out.inputs["Surface"])
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.eevee.taa_render_samples = 32
scene.render.resolution_x = scene.render.resolution_y = args.size
scene.render.resolution_percentage = 100
scene.render.film_transparent = True
scene.render.use_compositing = False
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.color_depth = "8"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure, scene.view_settings.gamma = 0, 1
for obj in scene.objects:
    obj.hide_render = True
data = bpy.data.cameras.new("Texture comparison camera")
data.lens, data.sensor_width, data.sensor_fit = 50, 36, "HORIZONTAL"
data.clip_start, data.clip_end = radius * 0.001, radius * 20
camera = bpy.data.objects.new("Texture comparison camera", data)
scene.collection.objects.link(camera)
scene.camera = camera
world = bpy.data.worlds.new("Texture comparison world")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.3, 0.3, 0.3, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.7
scene.world = world


def assign(materials):
    target.data.materials.clear()
    for value in materials:
        target.data.materials.append(value)


def render(path, visible):
    source.hide_render = visible != source
    target.hide_render = visible != target
    visible.hide_set(False)
    scene.render.filepath = str(path)
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)


views = []
for index, direction in enumerate(((0, -1, 0.16), (1, 0, 0.16), (0, 1, 0.16), (-1, 0, 0.16))):
    direction = np.asarray(direction, float)
    position = center + direction / np.linalg.norm(direction) * radius * 2.8
    c2w = look_at(position, center)
    camera.matrix_world = Matrix((c2w @ np.diag([1, -1, -1, 1])).tolist())
    render(output / f"view_{index:02d}_source.png", source)
    assign([material])
    image.colorspace_settings.name = "sRGB"
    render(output / f"view_{index:02d}_old_unlit.png", target)
    image.colorspace_settings.name = "Non-Color"
    render(output / f"view_{index:02d}_old_linear_interpretation.png", target)
    assign(old_materials)
    render(output / f"view_{index:02d}_old_lit_normal.png", target)
    views.append({"index": index, "camera_to_world_opencv": c2w.tolist()})

manifest = {"source_blend": bpy.data.filepath, "target": target.name, "source": source.name,
            "vertices": len(target.data.vertices), "faces": len(target.data.polygons),
            "old_report": str(target.get("remi_texture_report", "")), "old_albedo": old_albedo,
            "views": views, "resolution": args.size,
            "camera": {"lens_mm": 50, "sensor_width_mm": 36},
            "limitation": "Color comparison uses emission; lit normal diagnostic has a simple world, not the viewport HDRI"}
(output / "diagnostic.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print("TEXTURE_TRANSFER_DIAGNOSTIC", json.dumps(manifest), flush=True)
