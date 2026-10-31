"""Occluded source-layer matching, conservative normals, and honest hole filling."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.splat_project.local_gaussian import (
    assemble_local, depth_normals, geometric_corner_normals, project_local, render_local,
)
from integrations.splat_project.octree import GaussianOctree
from test_octree_projection import combine, gaussian
from test_splat_projection import plane_mesh, plane_splats


class LayerTests(unittest.TestCase):
    def test_foreground_source_does_not_paint_an_occluded_target_layer(self):
        source = combine(gaussian(scale=(0.2, 0.2, 0.001), opacity=1),
                         gaussian((0, 0, 0.08), (0.2, 0.2, 0.001), (0, 0, 1), opacity=1))
        tree = GaussianOctree(source)
        rendered, _half = render_local(tree, [[0, 0, 0]], [[0, 0, 1]], 0.1, 0.005, backend="NUMPY")
        self.assertEqual(rendered[0, 7], 1)
        np.testing.assert_allclose(rendered[0, :3], [1, 0, 0], atol=1e-6)

    def test_absent_hidden_source_does_not_borrow_foreground_color(self):
        tree = GaussianOctree(gaussian((0, 0, 0.025), (0.2, 0.2, 0.001), (0, 0, 1), opacity=1))
        rendered, _half = render_local(tree, [[0, 0, 0]], [[0, 0, 1]], 0.01, 0.01, backend="NUMPY")
        self.assertEqual(rendered[0, 7], 2)
        self.assertEqual(rendered[0, 3], 0)

    def test_color_uses_ordered_alpha_compositing_not_an_average(self):
        source = combine(gaussian(scale=(0.2, 0.2, 0.001), opacity=0.5),
                         gaussian((0, 0, -0.002), (0.2, 0.2, 0.001), (0, 1, 0), opacity=1))
        rendered, _half = render_local(GaussianOctree(source), [[0, 0, 0]], [[0, 0, 1]],
                                       0.01, 0.005, backend="NUMPY")
        np.testing.assert_allclose(rendered[0, :3], [0.5/0.9995, 0.4995/0.9995, 0], atol=1e-6)

    def test_a_ray_starting_inside_the_cloud_can_still_match_its_local_layer(self):
        rendered, _half = render_local(GaussianOctree(gaussian(opacity=1)), [[0, 0, 0]], [[0, 0, 1]],
                                       0.01, 0.005, backend="NUMPY")
        self.assertEqual(rendered[0, 7], 1)
        np.testing.assert_allclose(rendered[0, :3], [1, 0, 0], atol=1e-6)

    def test_many_weak_overlapping_gaussians_are_not_capped(self):
        tree = GaussianOctree(combine(*(gaussian(opacity=0.01) for _ in range(320))))
        rendered, _half = render_local(tree, [[0, 0, 0]], [[0, 0, 1]], 0.01, 0.005, backend="NUMPY")
        self.assertEqual(rendered[0, 7], 1)

    @unittest.skipUnless(importlib.util.find_spec("numba"), "Optional numba is missing")
    def test_native_and_reference_agree(self):
        source = combine(gaussian(opacity=1), gaussian((0, 0, 0.08), color=(0, 1, 0)))
        tree = GaussianOctree(source, leaf_size=1)
        points, normals = [[0, 0, 0], [0.1, 0, 0], [4, 4, 4]], [[0, 0, 1]]*3
        reference, _ = render_local(tree, points, normals, 0.1, 0.005, backend="NUMPY")
        native, _ = render_local(tree, points, normals, 0.1, 0.005, backend="NUMBA_CPU")
        np.testing.assert_allclose(native, reference, atol=2e-6)
        self.assertEqual(tree.trace_info["tracer_backend"], "NUMBA_CPU")

    def test_missing_native_dependency_uses_the_same_local_model(self):
        tree = GaussianOctree(gaussian(opacity=1))
        with mock.patch.dict(sys.modules, {"integrations.splat_project.local_gaussian_native": None}):
            rendered, _ = render_local(tree, [[0, 0, 0]], [[0, 0, 1]], 0.01, 0.005)
        self.assertEqual(rendered[0, 7], 1)
        self.assertIn("acceleration_fallback", tree.trace_info)


class NormalAndFillTests(unittest.TestCase):
    def test_geometric_frame_ignores_split_shading_normals(self):
        mesh = plane_mesh()
        normal = geometric_corner_normals(mesh["positions"], np.array([[0, 1, 2], [0, 2, 3]]))
        np.testing.assert_allclose(normal, np.tile([0, 0, 1], (2, 3, 1)), atol=1e-6)

    def test_mirrored_target_keeps_outward_geometry_normals(self):
        mesh = plane_mesh()
        mesh["positions"][..., 0] *= -1
        normal = geometric_corner_normals(mesh["positions"], orientation_sign=-1)
        np.testing.assert_allclose(normal, np.tile([0, 0, 1], (2, 3, 1)), atol=1e-6)

    def test_plane_depth_has_stable_normals_and_noise_falls_back(self):
        y, x = np.mgrid[0:24, 0:24]
        points = np.stack((x*0.01, y*0.01, np.zeros_like(x)), axis=-1).astype(float)
        reference = np.tile([0, 0, 1], (24, 24, 1)).astype(np.float32)
        covered, confidence = np.ones((24, 24), bool), np.ones((24, 24), np.float32)
        normals, _strength = depth_normals(points, reference, confidence, covered, 2, lambda: None)
        np.testing.assert_allclose(normals, reference, atol=1e-6)
        points[..., 2] = np.random.default_rng(18).normal(0, 0.2, (24, 24))
        normals, strength = depth_normals(points, reference, confidence, covered, 2, lambda: None)
        self.assertGreater(float(normals[..., 2].min()), 0.95)
        self.assertLess(float(strength.mean()), 0.05)

    def test_hole_filling_never_steals_from_another_island(self):
        covered = np.zeros((8, 8), bool)
        covered[:, :2], covered[:, 5:] = True, True
        positions = np.zeros((8, 8, 3), np.float32)
        normals = np.tile([0, 0, 1], (8, 8, 1)).astype(np.float32)
        tangents = np.tile([1, 0, 0], (8, 8, 1)).astype(np.float32)
        data = np.zeros((int(covered.sum()), 8))
        left = np.flatnonzero(np.nonzero(covered)[1] < 2)
        data[left, 0], data[left, 3], data[left, 7] = 1, 1, 1
        colors, _normal, confidence, *_ = assemble_local(
            {}, positions, normals, normals, tangents, np.ones((8, 8)), covered, data, 0,
            normal_mode="MESH", normal_strength=1, normal_smoothing=0, fill_holes=True,
            fill_background=True, margin_px=8, cancelled=lambda: None)
        np.testing.assert_allclose(colors[:, 5:], 0.5)
        np.testing.assert_array_equal(confidence[:, 5:], 0)

    def test_projection_is_color_faithful_without_density_normals(self):
        mesh = plane_mesh()
        mesh["positions"][..., 2] -= 0.01
        result = project_local(mesh, plane_splats(count=8), texture_size=64, normal_mode="MESH", backend="NUMPY")
        self.assertEqual(result.report["method"], "OCTREE_LOCAL")
        self.assertGreater(result.report["hits"], 3500)
        np.testing.assert_allclose(result.normal[32, 32], [0.5, 0.5, 1], atol=1e-5)

    def test_invalid_inputs_and_cancellation(self):
        with self.assertRaises(ValueError):
            project_local(plane_mesh(), plane_splats(), texture_size=64, match_distance=float("nan"))
        with self.assertRaises(ValueError):
            render_local(GaussianOctree(gaussian()), [[0, 0, 0]], [[0, 0, 2]], 0.01, 0.005)
        def cancel():
            raise InterruptedError("cancel")
        with self.assertRaises(InterruptedError):
            project_local(plane_mesh(), plane_splats(), texture_size=64, cancelled=cancel)

    def test_packaged_worker_uses_the_new_protocol(self):
        with tempfile.TemporaryDirectory(prefix="remi-local-gaussian-") as directory:
            root = Path(directory)
            np.savez(root/"mesh.npz", **plane_mesh())
            np.savez(root/"source.npz", **plane_splats(count=8))
            (root/"request.json").write_text(json.dumps({"mode": "octree_local", "texture_size": 64,
                "archive": str(root/"source.npz"), "mesh": str(root/"mesh.npz")}))
            completed = subprocess.run([sys.executable, "-I", "-B", str(ROOT/"integrations/splat_project/worker.py"),
                                        "--request", str(root/"request.json")], capture_output=True, text=True, timeout=120)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads((root/"report.json").read_text())
            self.assertEqual(report["method"], "OCTREE_LOCAL")
            for name in ("albedo.png", "normal.png", "confidence.png", "complete.json"):
                self.assertTrue((root/name).is_file())


if __name__ == "__main__":
    unittest.main()
