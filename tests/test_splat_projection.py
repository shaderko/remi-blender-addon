"""External projector test for UV-space Gaussian appearance baking (no Blender)."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


projection = module(
    "remi_splat_projection",
    ROOT / "integrations" / "splat_project" / "projection.py",
)
splat_data = module("remi_splat_data", ROOT / "integrations" / "triposplat" / "splat_data.py")


def available():
    return all(importlib.util.find_spec(name) for name in ("numpy", "scipy", "PIL"))


def plane_mesh():
    positions = np.array(
        [
            [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
            [[0, 0, 0], [1, 1, 0], [0, 1, 0]],
        ],
        dtype=np.float32,
    )
    normals = np.tile(np.array([[0, 0, 1]], dtype=np.float32), (2, 3, 1))
    uvs = np.array(
        [
            [[0, 0], [1, 0], [1, 1]],
            [[0, 0], [1, 1], [0, 1]],
        ],
        dtype=np.float32,
    )
    return {"positions": positions, "normals": normals, "uvs": uvs}


def plane_splats(count=16, color=(1.0, 0.0, 0.0), mask=None):
    values = np.linspace(0.02, 0.98, count, dtype=np.float32)
    x, y = np.meshgrid(values, values)
    positions = np.stack((x.ravel(), y.ravel(), np.zeros(count * count, np.float32)), axis=1)
    if mask is not None:
        positions = positions[mask(positions)]
    total = len(positions)
    scales = np.tile(np.array([0.08, 0.08, 0.002], np.float32), (total, 1))
    rotations = np.tile(np.array([1, 0, 0, 0], np.float32), (total, 1))
    sh_dc = np.tile(
        np.array(
            [(color[0] - 0.5) / projection.C0,
             (color[1] - 0.5) / projection.C0,
             (color[2] - 0.5) / projection.C0],
            np.float32,
        ),
        (total, 1),
    )
    return {
        "positions": positions.astype(np.float32),
        "scales": scales,
        "rotations": rotations,
        "opacities": np.ones(total, np.float32),
        "sh_dc": sh_dc,
    }


@unittest.skipUnless(available(), "numpy/scipy/Pillow are required for the projector")
class ProjectionTests(unittest.TestCase):
    def test_plane_projection_is_red_with_flat_normal(self):
        result = projection.project(
            plane_mesh(),
            plane_splats(),
            texture_size=128,
            samples=16,
            normal_source="AUTO",
            fill_holes=True,
        )
        self.assertGreater(result.report["coverage_mean"], 0.9)
        self.assertGreater(result.report["covered_texels"], 12000)
        center = result.albedo[64, 64]
        self.assertGreater(center[0], 0.95)
        self.assertLess(center[1], 0.05)
        self.assertTrue(np.allclose(result.normal[64, 64], (0.5, 0.5, 1.0), atol=0.03))
        self.assertGreater(result.confidence[64, 64], 0.9)

    def test_holes_are_filled_but_marked_low_confidence(self):
        result = projection.project(
            plane_mesh(),
            plane_splats(mask=lambda points: points[:, 0] < 0.45),
            texture_size=128,
            samples=16,
            fill_holes=True,
        )
        self.assertGreater(result.report["filled_texels"], 1000)
        # Right side has no Gaussian evidence; its colour comes from the fill and
        # its confidence is zero so the repair UI can target it.
        self.assertGreater(result.albedo[64, 100, 0], 0.9)
        self.assertEqual(float(result.confidence[64, 100]), 0.0)

    def test_mirrored_uv_flips_the_tangent_green_axis(self):
        normal = np.array([[0.0, 1.0, 1.0]], dtype=np.float32)
        normal /= np.linalg.norm(normal, axis=1, keepdims=True)
        tangent = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        mesh_normal = np.array([[0.0, 0.0, 1.0]], dtype=np.float32)
        upright = projection.encode_normal_map(
            normal, tangent, mesh_normal, np.array([1], dtype=np.int8)
        )
        mirrored = projection.encode_normal_map(
            normal, tangent, mesh_normal, np.array([-1], dtype=np.int8)
        )
        self.assertGreater(upright[0, 1], 0.8)
        self.assertLess(mirrored[0, 1], 0.2)

    def test_view_projection_uses_camera_image_and_alpha(self):
        mesh = plane_mesh()
        mesh["positions"] = mesh["positions"] - np.array([0.5, 0.5, 0.0], dtype=np.float32)
        view = np.eye(4, dtype=np.float64)
        view[2, 3] = -3.0
        f = 1.0 / np.tan(np.radians(50.0) / 2.0)
        near, far = 0.1, 100.0
        projection_matrix = np.array(
            [
                [f, 0, 0, 0],
                [0, f, 0, 0],
                [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
                [0, 0, -1, 0],
            ],
            dtype=np.float64,
        )
        image = np.ones((256, 256, 4), dtype=np.float32)
        image[..., 0] = 1.0
        image[..., 1] = 0.2
        image[..., 2] = 0.1
        image[85:100, 85:100, 3] = 0.0
        result = projection.project_from_view(
            mesh, image, view, projection_matrix, texture_size=128
        )
        self.assertGreater(result.report["visible_texels"], 10_000)
        self.assertGreater(result.layer[64, 64, 0], 0.9)
        self.assertGreater(result.layer[64, 64, 3], 0.9)
        # The alpha hole in the camera image becomes a hole in the layer.
        self.assertEqual(float(result.layer[10, 10, 3]), 0.0)

    def test_polygon_mask_uses_bottom_left_viewport_coordinates(self):
        polygon = [(0.0, 0.5), (1.0, 0.5), (1.0, 1.0), (0.0, 1.0)]
        mask = projection._polygon_mask(128, 128, polygon)
        self.assertGreater(mask[:64].mean(), 0.9)
        self.assertLess(mask[64:].mean(), 0.1)

        soft = projection._polygon_mask(128, 128, polygon, feather=8.0)
        self.assertGreater(soft[:64].mean(), 0.8)
        self.assertLess(soft[70:].mean(), 0.2)
        # The feathered boundary is not a hard step.
        self.assertTrue(np.any((soft > 0.05) & (soft < 0.95)))

    def test_unblend_view_recovers_straight_alpha(self):
        black = np.zeros((8, 8, 3), dtype=np.float32)
        white = np.zeros((8, 8, 3), dtype=np.float32)
        red_linear = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        alpha = np.float32(0.5)
        black[:] = projection._linear_to_srgb(red_linear * alpha)
        white[:] = projection._linear_to_srgb(red_linear * alpha + (1.0 - alpha))
        rgba = projection.unblend_view(black, white)
        self.assertAlmostEqual(float(rgba[0, 0, 3]), 0.5, places=2)
        self.assertGreater(float(rgba[0, 0, 0]), 0.9)
        self.assertLess(float(rgba[0, 0, 1]), 0.1)

        mesh = plane_mesh()
        mesh["positions"] = mesh["positions"] - np.array([0.5, 0.5, 0.0], dtype=np.float32)
        view = np.eye(4, dtype=np.float64)
        view[2, 3] = -3.0
        f = 1.0 / np.tan(np.radians(50.0) / 2.0)
        near, far = 0.1, 100.0
        projection_matrix = np.array(
            [
                [f, 0, 0, 0],
                [0, f, 0, 0],
                [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
                [0, 0, -1, 0],
            ],
            dtype=np.float64,
        )
        image = np.ones((256, 256, 4), dtype=np.float32)
        full = projection.project_from_view(
            mesh, image, view, projection_matrix, texture_size=128
        )
        left = projection.project_from_view(
            mesh,
            image,
            view,
            projection_matrix,
            texture_size=128,
            view_mask=[(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)],
            view_mask_feather=0.0,
        )
        self.assertTrue(left.report["area_mask"])
        self.assertLess(left.report["visible_texels"], full.report["visible_texels"])
        self.assertGreater(left.report["visible_texels"], 0)

    def test_view_projection_accepts_a_rendered_normal_pass(self):
        mesh = plane_mesh()
        mesh["positions"] = mesh["positions"] - np.array([0.5, 0.5, 0.0], dtype=np.float32)
        view = np.eye(4, dtype=np.float64)
        view[2, 3] = -3.0
        f = 1.0 / np.tan(np.radians(50.0) / 2.0)
        near, far = 0.1, 100.0
        projection_matrix = np.array(
            [
                [f, 0, 0, 0],
                [0, f, 0, 0],
                [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
                [0, 0, -1, 0],
            ],
            dtype=np.float64,
        )
        image = np.ones((256, 256, 4), dtype=np.float32)
        image[..., 0] = 1.0
        image[..., 1] = 0.2
        image[..., 2] = 0.1
        normal_image = np.zeros((256, 256, 4), dtype=np.float32)
        normal_image[..., :3] = (0.5, 0.85, 0.85)
        normal_image[..., 3] = 1.0
        result = projection.project_from_view(
            mesh,
            image,
            view,
            projection_matrix,
            texture_size=128,
            normal_image=normal_image,
            normal_smoothing=0,
        )
        self.assertIsNotNone(result.normal)
        self.assertGreater(float(result.normal[64, 64, 3]), 0.9)
        self.assertTrue(
            np.allclose(result.normal[64, 64, :3], (0.5, 0.85, 0.85), atol=0.03)
        )
        with tempfile.TemporaryDirectory(prefix="remi-view-normal-") as directory:
            path = projection.save_view_normal(result, Path(directory))
            self.assertTrue(Path(path).is_file())

    def test_worker_writes_images_and_completion(self):
        with tempfile.TemporaryDirectory(prefix="remi-splat-project-") as directory:
            root = Path(directory)
            mesh_path = root / "mesh.npz"
            mesh = plane_mesh()
            np.savez(mesh_path, **mesh)
            splat_path = root / "splats.npz"
            splat_data.save(splat_path, plane_splats())
            request = root / "request.json"
            request.write_text(
                json.dumps(
                    {
                        "archive": str(splat_path),
                        "mesh": str(mesh_path),
                        "texture_size": 128,
                        "samples": 16,
                        "normal_source": "AUTO",
                        "fill_holes": True,
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable, "-I", "-B", "-u",
                    str(ROOT / "integrations" / "splat_project" / "worker.py"),
                    "--request", str(request),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["texture_size"], 128)
            self.assertGreater(report["covered_texels"], 12000)
            for name in ("albedo.png", "normal.png", "confidence.png"):
                self.assertTrue((root / name).is_file(), name)
            self.assertTrue((root / "complete.json").is_file())


if __name__ == "__main__":
    unittest.main()
