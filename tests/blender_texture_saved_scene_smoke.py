"""Bake and verify fresh packed images in a loaded scene without saving its .blend.

blender --background --factory-startup --disable-autoexec scene.blend
  --python this_file -- --target ... --source ... --python python.exe --output fresh_dir
Use --installed to exercise the local installed extension instead of the repo.
"""
import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--target", required=True)
parser.add_argument("--source", required=True)
parser.add_argument("--python", required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--installed", action="store_true")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
scene_path = Path(bpy.data.filepath)
before_hash = hashlib.sha256(scene_path.read_bytes()).hexdigest()
if args.installed:
    name = "bl_ext.user_default.remi"
    assert bpy.ops.preferences.addon_enable(module=name) == {"FINISHED"}
    addon = importlib.import_module(name)
else:
    name = "remi"
    spec = importlib.util.spec_from_file_location(name, ROOT/"__init__.py", submodule_search_locations=[str(ROOT)])
    addon = importlib.util.module_from_spec(spec)
    sys.modules[name] = addon
    spec.loader.exec_module(addon)
    addon.register()
adapter = importlib.import_module(name+".blender.splat_projection")
layers = importlib.import_module(name+".blender.texture_layers")
operators = importlib.import_module(name+".features.texture.operators")
service = importlib.import_module(name+".features.texture.service")
client = importlib.import_module(name+".integrations.splat_project.client")
mesh, source = bpy.data.objects[args.target], bpy.data.objects[args.source]
scene = next(scene for scene in bpy.data.scenes if mesh.name in scene.objects)
bpy.context.window.scene = scene
bpy.context.view_layer.objects.active = mesh
mesh.select_set(True)
original_vertices = len(mesh.data.vertices)
old_images = {node.image for material in mesh.data.materials for node in material.node_tree.nodes
              if node.type == "TEX_IMAGE" and node.image is not None}
old_samples = {image.name: tuple(image.pixels[:64]) for image in old_images}
settings = scene.remi_texture
settings.texture_size = "1024"
settings.smooth_mesh = True
uv_name = operators._prepare_projection_mesh(bpy.context, mesh, settings, bpy.context.window_manager.remi_texture)
adapter.export_mesh_archive(bpy.context, mesh, output/"mesh.npz")
adapter.write_world_splat_archive(source, output/"source.npz")
started = time.perf_counter()
job = client.ProjectionJob.start(python=args.python, archive=output/"source.npz", mesh=output/"mesh.npz",
                                 output=output/"textures", method="OCTREE", texture_size=1024, source=source.name)
try:
    job.process.wait(timeout=180)
    result = job.result()
finally:
    job.cancel()
wall = time.perf_counter()-started
service.import_result(mesh, result, source_name=source.name, pack_images=True,
                      normal_mode="PROJECTED", uv_name=uv_name)
layers.sync_base_layer(mesh)
layers.rebuild_layer_material(mesh)
errors = {}
for key, colorspace in (("albedo", "sRGB"), ("normal", "Non-Color")):
    packed = bpy.data.images[mesh[f"remi_texture_{key}_image"]]
    assert packed not in old_images and packed.packed_file is not None
    fresh = bpy.data.images.load(result[key], check_existing=False)
    fresh.colorspace_settings.name = colorspace
    actual = np.empty(len(packed.pixels), np.float32)
    expected = np.empty(len(fresh.pixels), np.float32)
    packed.pixels.foreach_get(actual)
    fresh.pixels.foreach_get(expected)
    errors[key] = float(np.abs(actual-expected).max())
    assert errors[key] == 0, errors
    assert any(node.type == "TEX_IMAGE" and node.image == packed for node in mesh.data.materials[0].node_tree.nodes)
for image in old_images:
    assert tuple(image.pixels[:64]) == old_samples[image.name]
assert len(mesh.data.vertices) == original_vertices
assert hashlib.sha256(scene_path.read_bytes()).hexdigest() == before_hash
summary = {"addon": addon.__file__, "source_scene_unmodified": True, "vertices": original_vertices,
           "packed_image_max_errors": errors, "worker_wall_seconds": wall, "report": result["report_data"]}
(output/"validation.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print("REMI_SAVED_SCENE_TEXTURE_SMOKE_OK", json.dumps(summary), flush=True)
