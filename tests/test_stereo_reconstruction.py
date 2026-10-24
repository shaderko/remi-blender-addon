"""CPU geometry/filter/fusion tests; no Blender, checkpoints, or network required."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from splat_reconstruction.geometry import archive_bounds, look_at, make_rig, stereo_cameras
from splat_reconstruction.stereo import disparity_to_depth, sample_horizontal
from splat_reconstruction.fusion import TSDF, color_vertices, project
from splat_reconstruction.metrics import image_metrics, topology_metrics


BOUNDS = {"low": [-0.6, -0.6, -0.6], "high": [0.6, 0.6, 0.6],
          "center": [0, 0, 0], "radius": 1.04}


class CameraTests(unittest.TestCase):
    def test_parallel_stereo_has_positive_metric_disparity(self):
        left, right = stereo_cameras([1, -2, 1], [0, 0, 0], 0.2)
        np.testing.assert_allclose(left[:3, :3], right[:3, :3])
        self.assertAlmostEqual(np.linalg.det(left[:3, :3]), 1)
        local = np.array([0.12, -0.08, 2.0])
        point = left[:3, :3] @ local + left[:3, 3]
        in_right = (point - right[:3, 3]) @ right[:3, :3]
        self.assertAlmostEqual(in_right[1], local[1])
        self.assertAlmostEqual(in_right[2], local[2])
        disparity = 500 * (local[0] / local[2] - in_right[0] / in_right[2])
        self.assertAlmostEqual(disparity, 500 * 0.2 / local[2])

    def test_holdouts_are_disjoint_and_deterministic(self):
        rig = make_rig(BOUNDS, views=12, evaluation_views=4, resolution=128)
        self.assertEqual(rig, make_rig(BOUNDS, views=12, evaluation_views=4, resolution=128))
        self.assertEqual(len(rig["pairs"]), 12)
        self.assertEqual(len(rig["evaluation_views"]), 4)
        for pair in rig["pairs"]:
            l, r = [np.asarray(pair[f"{side}_camera_to_world_opencv"]) for side in ("left", "right")]
            for view in rig["evaluation_views"]:
                eval_position = np.asarray(view["camera_to_world_opencv"])[:3, 3]
                self.assertGreater(np.linalg.norm((l[:3, 3] + r[:3, 3]) / 2 - eval_position), 0.1)

    def test_invalid_camera_and_baseline_fail(self):
        with self.assertRaises(ValueError):
            look_at([0, 0, 0], [0, 0, 0])
        with self.assertRaises(ValueError):
            stereo_cameras([0, 0, 2], [0, 0, 0], -0.1)
        with self.assertRaises(ValueError):
            make_rig(BOUNDS, distance=1)

    def test_full_bounds_do_not_drop_outlying_thin_parts(self):
        arrays = {"positions": np.array([[0, 0, 0], [0, 0, 10]]),
                  "opacities": np.ones(2), "scales": np.full((2, 3), 0.01)}
        self.assertGreater(archive_bounds(arrays)["high"][2], 10)


class StereoFilterTests(unittest.TestCase):
    def depth(self, left, right, alpha=None, **kwargs):
        alpha = np.ones_like(left) if alpha is None else alpha
        return disparity_to_depth(left, right, alpha, np.ones_like(right),
                                  focal=100, baseline=0.2, near=0.1, far=10, **kwargs)

    def test_metric_depth_and_out_of_frame_occlusion(self):
        disparity = np.full((3, 12), 4, dtype=float)
        depth, confidence = self.depth(disparity, disparity)
        np.testing.assert_array_equal(depth[:, :4], 0)
        np.testing.assert_allclose(depth[:, 4:], 5)
        np.testing.assert_allclose(confidence[:, 4:], 1)

    def test_invalid_disparity_and_alpha_never_become_geometry(self):
        left = np.full((2, 12), 4, dtype=float)
        left[0, 4:8] = [0, -1, np.inf, np.nan]
        alpha = np.ones_like(left)
        alpha[1] = 0
        depth, confidence = self.depth(left, np.full_like(left, 4), alpha)
        self.assertTrue(np.isfinite(depth).all())
        self.assertTrue(np.isfinite(confidence).all())
        np.testing.assert_array_equal(depth[0, 4:8], 0)
        np.testing.assert_array_equal(depth[1], 0)

    def test_left_right_mismatch_rejected(self):
        depth, _ = self.depth(np.full((2, 12), 4), np.full((2, 12), 7))
        self.assertFalse(depth.any())

    def test_subpixel_sampling_and_finite_support(self):
        image = np.array([[1.0, 3.0, np.nan]])
        value, valid = sample_horizontal(image, np.array([[0.5, 1, 1.5]]))
        np.testing.assert_array_equal(valid, [[True, True, False]])
        np.testing.assert_allclose(value[:, :2], [[2, 3]])

    def test_no_interpolation_across_invalid_right_depth(self):
        left = np.full((2, 16), 4.5)
        right = np.full_like(left, 4.5)
        right[:, 3] = 0
        depth, _ = self.depth(left, right, absolute_tolerance=10)
        self.assertEqual(depth[0, 8], 0)

    def test_reversed_near_far_and_shape_fail(self):
        with self.assertRaises(ValueError):
            disparity_to_depth(np.ones((2, 2)), np.ones((2, 2)), np.ones((2, 2)), np.ones((2, 2)),
                               focal=100, baseline=0.1, near=10, far=1)
        with self.assertRaises(ValueError):
            self.depth(np.ones((2, 2)), np.ones((3, 2)))


class FusionTests(unittest.TestCase):
    def test_project_uses_camera_z_not_ray_distance(self):
        k = [[100, 0, 10], [0, 100, 10], [0, 0, 1]]
        u, v, z, valid = project(np.array([[0.1, 0.1, 2], [0, 0, -2]]), np.eye(4), k, (32, 32))
        self.assertEqual((u[0], v[0], z[0]), (15, 15, 2))
        np.testing.assert_array_equal(valid, [True, False])

    def test_empty_unknown_volume_never_emits_a_surface(self):
        volume = TSDF(BOUNDS, 24)
        volume.integrate(np.zeros((32, 32)), np.ones((32, 32)), np.eye(4), np.eye(3))
        with self.assertRaisesRegex(RuntimeError, "No observed surface"):
            volume.extract()

    def test_metric_sphere_survives_fusion(self):
        # Analytic ray/sphere intersection is test-only ground truth, never a
        # substitute for the learned stereo used by the real reconstruction.
        rig = make_rig(BOUNDS, views=16, evaluation_views=4, resolution=96)
        k = np.asarray(rig["intrinsics"])
        yy, xx = np.mgrid[:96, :96]
        rays = np.stack(((xx - k[0, 2]) / k[0, 0], (yy - k[1, 2]) / k[1, 1], np.ones_like(xx)), axis=-1)
        volume = TSDF(BOUNDS, 48)
        for pair in rig["pairs"]:
            camera = np.asarray(pair["left_camera_to_world_opencv"])
            direction = rays @ camera[:3, :3].T
            origin = camera[:3, 3]
            a = (direction * direction).sum(axis=-1)
            b = 2 * (direction * origin).sum(axis=-1)
            c = origin @ origin - 0.5 ** 2
            discriminant = b * b - 4 * a * c
            depth = np.where(discriminant >= 0, (-b - np.sqrt(np.maximum(discriminant, 0))) / (2 * a), 0)
            volume.integrate(depth, (depth > 0).astype(float), camera, k)
        vertices, faces = volume.extract()
        self.assertGreater(len(faces), 1000)
        error = np.abs(np.linalg.norm(vertices, axis=1) - 0.5)
        self.assertLess(np.quantile(error, 0.95), volume.voxel * 1.5)
        import trimesh
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        self.assertGreater(mesh.volume, 0)

    def test_depth_tested_color_does_not_paint_hidden_vertices(self):
        vertices = np.array([[0, 0, 1], [0, 0, 2]], dtype=float)
        observation = (np.ones((3, 3)), np.ones((3, 3)), np.full((3, 3, 3), [255, 0, 0]), np.eye(4))
        colors, coverage = color_vertices(vertices, [observation], [[1, 0, 1], [0, 1, 1], [0, 0, 1]], 0.1)
        np.testing.assert_array_equal(colors[0], [255, 0, 0])
        np.testing.assert_array_equal(colors[1], [127, 127, 127])
        self.assertEqual(coverage, 0.5)


class MetricTests(unittest.TestCase):
    def test_missing_and_extra_geometry_are_separate(self):
        ref = np.zeros((4, 4, 4), dtype=np.uint8)
        ref[:2, :, 3] = 255
        pred = ref.copy()
        pred[0, :, 3] = 0
        pred[2, :, 3] = 255
        metrics = image_metrics(ref, pred)
        self.assertEqual(metrics["missing_foreground_fraction"], 0.5)
        self.assertEqual(metrics["extra_foreground_fraction"], 0.5)
        self.assertAlmostEqual(metrics["silhouette_iou"], 1 / 3)

    def test_open_triangle_is_not_mislabeled_as_three_holes(self):
        import trimesh
        mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]], process=False)
        metrics = topology_metrics(mesh)
        self.assertEqual(metrics["boundary_edges"], 3)
        self.assertEqual(metrics["components"], 1)
        self.assertNotIn("holes", metrics)


class ColmapExportTests(unittest.TestCase):
    def test_export_excludes_heldout_views_and_preserves_camera_pose(self):
        from PIL import Image
        from scipy.spatial.transform import Rotation
        from export_splat_colmap import export
        with tempfile.TemporaryDirectory(prefix="remi-stereo-colmap-") as directory:
            root = Path(directory)
            archive = root / "splats.npz"
            np.savez(archive, schema=np.array(1), positions=np.array([[0, 0, 0]], np.float32),
                     scales=np.ones((1, 3), np.float32) * 0.1,
                     rotations=np.array([[1, 0, 0, 0]], np.float32),
                     opacities=np.ones(1, np.float32), sh_dc=np.zeros((1, 3), np.float32))
            views = root / "views"
            views.mkdir()
            rig = make_rig(BOUNDS, views=2, evaluation_views=1, resolution=64)
            rig.update(source_archive=str(archive), source_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
            (views / "dataset.json").write_text(json.dumps(rig))
            (views / "render_complete.json").write_text('{"complete": true}')
            for pair in rig["pairs"]:
                for side in ("left", "right"):
                    path = views / pair[side]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    Image.new("RGBA", (64, 64), (120, 60, 20, 128)).save(path)
            result = export(views, root / "colmap", include_right=True)
            self.assertEqual(len(result["images"]), 4)
            self.assertEqual(result["withheld_evaluation_views"], 1)
            # Held-out image deliberately does not exist: exporting must never read it.
            self.assertEqual(len(list((root / "colmap/images").glob("*.png"))), 4)
            lines = (root / "colmap/sparse/0/images.txt").read_text().splitlines()
            tokens = lines[1].split()
            quaternion = np.array(tokens[1:5], dtype=float)
            translation = np.array(tokens[5:8], dtype=float)
            matrix = np.eye(4)
            matrix[:3, :3] = Rotation.from_quat(quaternion[[1, 2, 3, 0]]).as_matrix()
            matrix[:3, 3] = translation
            np.testing.assert_allclose(matrix, np.linalg.inv(rig["pairs"][0]["left_camera_to_world_opencv"]), atol=1e-10)
            with self.assertRaisesRegex(ValueError, "Output is not empty"):
                export(views, root / "colmap")


if __name__ == "__main__":
    unittest.main()
