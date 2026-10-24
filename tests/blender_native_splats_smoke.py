"""Render an existing generation using Blender 5.3's real Gaussian primitive.

Run with --factory-startup --background --python-exit-code 1 --python ... --
--archive <splats.npz>. No generation or external Python dependencies required.
"""
import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import time

import bpy
from mathutils import Vector
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--output", type=Path)
parser.add_argument("--engine", choices=("BOTH", "BLENDER_EEVEE", "CYCLES"), default="BOTH")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
archive = args.archive.resolve()
output = args.output or archive.parent / ("native_blender53_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
output.mkdir(parents=True, exist_ok=False)

spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
from remi.blender.gaussian_splats import import_splats, native_splats_available
from remi.integrations.triposplat.splat_data import load

assert native_splats_available(), "This Blender build does not expose native GAUSSIAN_SPLAT point clouds"
data = load(archive)
obj = import_splats(bpy.context, archive, display="NATIVE", preview_limit=1)
assert obj.type == "POINTCLOUD" and obj.data.type == "GAUSSIAN_SPLAT"
assert len(obj.data.points) == len(data["positions"])
assert not obj.modifiers and not obj.data.materials
assert "torch" not in sys.modules


def attribute(cloud, name, width, property_name="vector"):
    values = np.empty(len(cloud.points) * width, dtype=np.float32)
    cloud.attributes[name].data.foreach_get(property_name, values)
    return values.reshape(-1, width)


np.testing.assert_array_equal(attribute(obj.data, "position", 3), data["positions"])
np.testing.assert_array_equal(attribute(obj.data, "scale", 3), data["scales"])
np.testing.assert_array_equal(attribute(obj.data, "rotation", 4, "value"), data["rotations"])
np.testing.assert_array_equal(attribute(obj.data, "radiance:base", 4), np.column_stack((data["sh_dc"], data["opacities"])))

# Independently compare against Blender's own Gaussian PLY importer. This checks
# the renderer contract rather than only comparing our code against itself.
reference_path = archive.with_name("model.ply")
assert reference_path.is_file(), reference_path
assert bpy.ops.wm.ply_import(filepath=str(reference_path), forward_axis="Y", up_axis="Z") == {"FINISHED"}
reference = bpy.context.active_object
assert reference.type == "POINTCLOUD" and reference.data.type == "GAUSSIAN_SPLAT"
np.testing.assert_array_equal(attribute(reference.data, "position", 3), data["positions"])
np.testing.assert_allclose(attribute(reference.data, "scale", 3), data["scales"], rtol=1e-5)
np.testing.assert_allclose(attribute(reference.data, "radiance:base", 4), np.column_stack((data["sh_dc"], data["opacities"])), atol=1e-6)
rotations = attribute(reference.data, "rotation", 4, "value")
np.testing.assert_allclose(np.abs(np.sum(rotations * data["rotations"], axis=1)), 1, atol=1e-6)

for other in list(bpy.context.scene.objects):
    if other is not obj:
        bpy.data.objects.remove(other, do_unlink=True)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj

low, high = np.quantile(data["positions"], [0.01, 0.99], axis=0)
center = Vector((low + high) / 2)
extent = max(float(np.max(high - low)), 0.1)
camera_data = bpy.data.cameras.new("Native Gaussian Camera")
camera = bpy.data.objects.new("Native Gaussian Camera", camera_data)
bpy.context.scene.collection.objects.link(camera)
camera.location = center + Vector((1.6, -3.0, 1.2)) * extent
camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
camera_data.type = "ORTHO"
camera_data.ortho_scale = extent * 1.6
scene = bpy.context.scene
scene.camera = camera
scene.render.resolution_x = scene.render.resolution_y = 640
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = True
scene.view_settings.view_transform = "Standard"
report = {
    "blender": bpy.app.version_string, "build": bpy.app.build_hash.decode(),
    "archive": str(archive), "gaussians": len(obj.data.points),
    "object_type": obj.type, "pointcloud_type": obj.data.type,
    "native_ply_parity": True, "modifiers": len(obj.modifiers), "materials": len(obj.data.materials),
    "renders": {},
}
engines = ("BLENDER_EEVEE", "CYCLES") if args.engine == "BOTH" else (args.engine,)
for engine in engines:
    scene.render.engine = engine
    if engine == "CYCLES":
        scene.cycles.samples = 32
        scene.cycles.use_denoising = False
    path = output / f"native_{engine.lower()}.png"
    scene.render.filepath = str(path)
    started = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    elapsed = time.perf_counter() - started
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        rgba = pixels.reshape(-1, 4)
        visible = rgba[:, 3] > 0.05
        assert visible.sum() > 100, f"{engine}: native Gaussians are invisible"
        assert np.std(rgba[visible, :3]) > 0.01, f"{engine}: native colors are missing"
        report["renders"][engine] = {"seconds": elapsed, "visible_pixels": int(visible.sum()), "path": str(path)}
    finally:
        bpy.data.images.remove(image)
    print(f"NATIVE SPLATS RENDERED: {engine}: {path}", flush=True)
scene.render.engine = "BLENDER_EEVEE"
bpy.ops.wm.save_as_mainfile(filepath=str(output / "native_splats.blend"))
(output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
remi.unregister()
print(f"NATIVE GAUSSIAN SMOKE PASSED: {output}", flush=True)
