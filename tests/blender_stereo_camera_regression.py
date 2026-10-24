"""Verify stored OpenCV intrinsics/extrinsics against Blender's real camera API."""
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from splat_reconstruction.geometry import make_rig


bounds = {"low": [-0.5] * 3, "high": [0.5] * 3, "center": [0, 0, 0], "radius": 0.87}
rig = make_rig(bounds, views=8, evaluation_views=3, resolution=512)
scene = bpy.context.scene
scene.render.resolution_x = scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
data = bpy.data.cameras.new("Calibration test")
data.lens, data.sensor_width = rig["lens_mm"], rig["sensor_width_mm"]
data.sensor_fit = "HORIZONTAL"
camera = bpy.data.objects.new("Calibration test", data)
scene.collection.objects.link(camera)
scene.camera = camera
k = np.asarray(rig["intrinsics"])
flip = np.diag([1.0, -1.0, -1.0, 1.0])
points = np.array([[0, 0, 0], [0.2, -0.1, 0.3], [-0.3, 0.15, -0.2]])
for pair in rig["pairs"]:
    coordinates = {}
    for side in ("left", "right"):
        c2w = np.asarray(pair[f"{side}_camera_to_world_opencv"])
        camera.matrix_world = Matrix((c2w @ flip).tolist())
        bpy.context.view_layer.update()
        local = (points - c2w[:3, 3]) @ c2w[:3, :3]
        expected = (local @ k.T)[:, :2] / local[:, 2, None]
        actual = []
        for point in points:
            ndc = world_to_camera_view(scene, camera, Vector(point))
            actual.append([ndc.x * 512 - 0.5, (1 - ndc.y) * 512 - 0.5])
        np.testing.assert_allclose(actual, expected, atol=1e-4)
        coordinates[side] = expected
    np.testing.assert_allclose(coordinates["left"][:, 1], coordinates["right"][:, 1], atol=1e-8)
    assert np.all(coordinates["left"][:, 0] > coordinates["right"][:, 0])
print("Blender/OpenCV stereo camera calibration passed", flush=True)
