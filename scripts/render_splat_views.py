"""Render RGB and normalized depth views of a Remi Gaussian archive.

Run inside Blender 5.3+ (native GAUSSIAN_SPLAT support):
  blender --background --factory-startup --python render_splat_views.py -- \
      --archive path/to/splats.npz --output path/to/views --views 48 --resolution 512

The output is an ordinary posed multi-view dataset. Geometry is supplied by the
native splat renderer; no learned depth model is involved in this stage.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--views", type=int, default=48)
parser.add_argument("--resolution", type=int, default=512)
parser.add_argument("--engine", choices=("BLENDER_EEVEE", "CYCLES"), default="BLENDER_EEVEE")
parser.add_argument("--samples", type=int, default=16)
parser.add_argument("--lens", type=float, default=50.0)
parser.add_argument("--sensor-width", type=float, default=36.0)
parser.add_argument("--distance", type=float, default=2.6, help="camera distance / bounds radius")
parser.add_argument("--elevation-min", type=float, default=-30.0)
parser.add_argument("--elevation-max", type=float, default=70.0)
parser.add_argument("--seed", type=int, default=20261024)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])

archive = args.archive.resolve()
output = args.output.resolve()
if not archive.is_file():
    raise FileNotFoundError(archive)
if args.views < 8:
    raise ValueError("At least 8 views are required")
if args.resolution < 128:
    raise ValueError("Resolution must be at least 128")
output.mkdir(parents=True, exist_ok=True)

spec = importlib.util.spec_from_file_location(
    "remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)],
)
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
from remi.blender.gaussian_splats import import_splats  # noqa: E402
from remi.integrations.triposplat.splat_data import load  # noqa: E402

# A factory-startup scene contains a cube, light, and camera. Keep none of them.
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for datablocks in (bpy.data.meshes, bpy.data.cameras, bpy.data.lights):
    for datablock in list(datablocks):
        if datablock.users == 0:
            datablocks.remove(datablock)

data = load(archive)
positions = data["positions"]
low, high = np.quantile(positions, [0.01, 0.99], axis=0)
center = (low + high) / 2
radius = max(float(np.max(high - low)) / 2, 1e-4)
splat = import_splats(bpy.context, archive, display="NATIVE")

scene = bpy.context.scene
scene.render.engine = args.engine
scene.render.resolution_x = scene.render.resolution_y = args.resolution
scene.render.resolution_percentage = 100
scene.render.film_transparent = True
# The depth pass is encoded as a grayscale image. Standard + sRGB display
# transform is inverted exactly by the reconstruction script. AgX would add a
# non-invertible tone curve.
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure = 0.0
scene.view_settings.gamma = 1.0
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.color_depth = "8"
scene.render.use_file_extension = False
scene.render.threads_mode = "FIXED"
scene.render.threads = 8
if args.engine == "CYCLES":
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = False
else:
    scene.eevee.taa_render_samples = args.samples

camera_data = bpy.data.cameras.new("Remi Reconstruction Camera")
camera_data.lens = args.lens
camera_data.sensor_width = args.sensor_width
camera_data.sensor_fit = "HORIZONTAL"
camera_data.clip_start = max(radius * 0.01, 1e-5)
camera_data.clip_end = radius * 8.0
camera = bpy.data.objects.new("Remi Reconstruction Camera", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera

width, height = args.resolution, args.resolution
fx = width * args.lens / args.sensor_width
fy = fx
cx = cy = width / 2.0
intrinsics = [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]]

# Deterministic Fibonacci-sphere directions, restricted to a useful elevation
# band. Two extra poles ensure the top/bottom are not completely unobserved.
rng = np.random.default_rng(args.seed)
count = args.views
indices = np.arange(count, dtype=np.float64) + 0.5
phi = np.arccos(1 - 2 * indices / count)
# Golden-angle azimuth is more uniform than a random azimuth.
theta = np.pi * (1 + np.sqrt(5.0)) * indices
elevation_min = math.radians(args.elevation_min)
elevation_max = math.radians(args.elevation_max)
directions = []
for p, t in zip(phi, theta):
    elevation = math.asin(math.cos(p))  # cos(p) in [-1, 1]
    if not elevation_min <= elevation <= elevation_max:
        elevation = elevation_min + (elevation_max - elevation_min) * (
            (math.sin(p) + 1.0) / 2.0
        )
    azimuth = t
    directions.append((
        math.cos(elevation) * math.cos(azimuth),
        math.cos(elevation) * math.sin(azimuth),
        math.sin(elevation),
    ))
directions = np.asarray(directions, dtype=np.float64)

# A depth compositor maps Blender Z to a normalized 16-bit image. The RGB pass
# is rendered separately with compositing disabled.
bpy.context.view_layer.use_pass_z = True
depth_group = bpy.data.node_groups.new("Remi Reconstruction Depth", "CompositorNodeTree")
depth_output = depth_group.interface.new_socket(
    name="Image", in_out="OUTPUT", socket_type="NodeSocketColor",
)
layers = depth_group.nodes.new("CompositorNodeRLayers")
group_output = depth_group.nodes.new("NodeGroupOutput")
map_range = depth_group.nodes.new("ShaderNodeMapRange")
map_range.inputs["From Min"].default_value = 0.0
map_range.inputs["From Max"].default_value = camera_data.clip_end
map_range.inputs["To Min"].default_value = 0.0
map_range.inputs["To Max"].default_value = 1.0
map_range.clamp = True
depth_group.links.new(layers.outputs["Depth"], map_range.inputs["Value"])
depth_group.links.new(map_range.outputs["Result"], group_output.inputs["Image"])
scene.compositing_node_group = depth_group

views = []
for index, direction in enumerate(directions):
    view_dir = output / f"view_{index:04d}"
    view_dir.mkdir(parents=True, exist_ok=True)
    camera.location = Vector(center + direction * radius * args.distance)
    camera.rotation_euler = (Vector(center) - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    camera_matrix = np.array(camera.matrix_world, dtype=np.float64)
    # Blender camera looks down -Z with +Y up; OpenCV looks down +Z with +Y
    # down. Flipping local Y and Z gives a direct c2w matrix for OpenCV.
    cv_c2w = camera_matrix @ np.diag([1.0, -1.0, -1.0, 1.0])
    cv_w2c = np.linalg.inv(cv_c2w)
    scene.render.use_compositing = False
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = str(view_dir / "rgb.png")
    bpy.ops.render.render(write_still=True)
    scene.render.use_compositing = True
    scene.render.image_settings.color_mode = "BW"
    scene.render.image_settings.color_depth = "16"
    scene.render.filepath = str(view_dir / "depth.png")
    bpy.ops.render.render(write_still=True)
    views.append({
        "index": index,
        "rgb": "rgb.png",
        "depth": "depth.png",
        "camera_to_world_opencv": cv_c2w.tolist(),
        "world_to_camera_opencv": cv_w2c.tolist(),
        "intrinsics": intrinsics,
        "near": camera_data.clip_start,
        "far": camera_data.clip_end,
        "position": list(camera.location),
        "direction": direction.tolist(),
    })
    scene.render.use_compositing = False
    print(f"Rendered view {index + 1}/{count}", flush=True)

metadata = {
    "schema": 1,
    "source_archive": str(archive),
    "engine": args.engine,
    "resolution": [width, height],
    "lens_mm": args.lens,
    "sensor_width_mm": args.sensor_width,
    "intrinsics": intrinsics,
    "bounds": {"center": center.tolist(), "radius": radius},
    "depth_encoding": "uint16 gray / 65535 * far (background is far)",
    "views": views,
}
(output / "views.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
print(f"Wrote {len(views)} posed views to {output}", flush=True)
