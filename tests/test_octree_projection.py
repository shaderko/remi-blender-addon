"""Analytic surface hits, support bounds, missing evidence, and worker exchange."""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.splat_project.octree import GaussianOctree, project_octree, ray_box_interval
from integrations.splat_project.projection import encode_normal_map, project, rasterize_uv
from test_splat_projection import plane_mesh, plane_splats


def gaussian(position=(0, 0, 0), scale=(0.3, 0.4, 0.2), color=(1, 0, 0), opacity=0.9):
    return {"positions": np.array([position], np.float32), "scales": np.array([scale], np.float32),
            "rotations": np.array([[1, 0, 0, 0]], np.float32),
            "opacities": np.array([opacity], np.float32),
            "sh_dc": (np.array([color], np.float32) - 0.5) / 0.28209479177387814}


def combine(*sources):
    return {name: np.concatenate([source[name] for source in sources]) for name in sources[0]}


class OctreeTests(unittest.TestCase):
    def test_single_gaussian_front_hit_matches_analytic_surface_and_gradient(self):
        tree = GaussianOctree(gaussian())
        result = tree.trace([[0, 0, 1]], [[0, 0, -1]], 2, density=0.5)
        self.assertTrue(result["hit"][0])
        expected_z = 0.2 * np.sqrt(2 * np.log(0.9 / 0.5))
        self.assertAlmostEqual(result["distance"][0], 1 - expected_z, delta=5e-5)
        np.testing.assert_allclose(result["normals"][0], [0, 0, 1], atol=1e-6)
        np.testing.assert_allclose(result["colors"][0], [1, 0, 0], atol=1e-6)

    def test_first_hit_does_not_take_the_occluded_back_surface(self):
        data = combine(gaussian((0, 0, 0.3), (0.1, 0.1, 0.04), (1, 0, 0)),
                       gaussian((0, 0, -0.3), (0.1, 0.1, 0.04), (0, 0, 1)))
        result = GaussianOctree(data, leaf_size=1).trace([[0, 0, 1]], [[0, 0, -1]], 2)
        self.assertTrue(result["hit"][0])
        self.assertLess(result["distance"][0], 0.7)
        np.testing.assert_allclose(result["colors"][0], [1, 0, 0], atol=1e-6)

    def test_subthreshold_gaussians_combine_into_a_surface(self):
        data = combine(gaussian(opacity=0.3), gaussian(opacity=0.3))
        result = GaussianOctree(data).trace([[0, 0, 1]], [[0, 0, -1]], 2)
        self.assertTrue(result["hit"][0])
        expected = 1 - 0.2 * np.sqrt(2 * np.log(0.6 / 0.5))
        self.assertAlmostEqual(result["distance"][0], expected, delta=5e-5)

    def test_octree_bounds_include_broad_support_outside_center_cell(self):
        data = combine(gaussian((0, 0, 0), (2, 0.2, 0.1)),
                       gaussian((4, 4, 0), (0.1, 0.1, 0.1), (0, 1, 0)))
        split = GaussianOctree(data, leaf_size=1)
        flat = GaussianOctree(data, leaf_size=256)
        self.assertGreater(len(split.nodes), 1)
        ray = ([[2, 0, 1]], [[0, 0, -1]], 2)
        expected, actual = flat.trace(*ray), split.trace(*ray)
        self.assertTrue(actual["hit"][0])
        np.testing.assert_allclose(actual["distance"], expected["distance"], atol=1e-9)
        np.testing.assert_allclose(actual["normals"], expected["normals"], atol=1e-7)

    def test_parallel_box_rays_on_boundary_and_outside(self):
        _near, _far, hit = ray_box_interval(
            np.array([[0, 0, 2], [2, 0, 2]], float), np.array([[0, 0, -1]] * 2, float),
            np.array([0, -1, -1]), np.array([1, 1, 1]), np.array([4, 4]))
        np.testing.assert_array_equal(hit, [True, False])

    def test_starting_inside_cage_is_not_faked_as_a_source_surface(self):
        tree = GaussianOctree(gaussian())
        result = tree.trace([[0, 0, 0], [4, 4, 4]], [[0, 0, 1]] * 2, 2)
        np.testing.assert_array_equal(result["hit"], [False, False])
        np.testing.assert_array_equal(result["clipped"], [True, False])
        self.assertFalse(result["step_limit"].any())

    def test_step_budget_is_reported_as_missing_evidence_not_a_hit(self):
        centers = np.linspace(-1, 1, 32)
        data = combine(*(gaussian((0, 0, float(z)), (0.2, 0.2, 0.03), opacity=0.01) for z in centers))
        result = GaussianOctree(data).trace([[0, 0, 2]], [[0, 0, -1]], 4, max_steps=8)
        self.assertFalse(result["hit"][0])
        self.assertTrue(result["step_limit"][0])

    def test_memory_splitting_does_not_drop_gaussians_or_change_hits(self):
        data = combine(gaussian(), gaussian((0.5, 0, 0), color=(0, 1, 0)))
        tree = GaussianOctree(data)
        origins = [[0, 0, 1], [0.1, 0, 1], [0.2, 0, 1], [0.3, 0, 1]]
        first = tree.trace(origins, [[0, 0, -1]] * 4, 2)
        split = tree.trace(origins, [[0, 0, -1]] * 4, 2, max_pairs=2)
        np.testing.assert_array_equal(split["hit"], first["hit"])
        np.testing.assert_allclose(split["distance"], first["distance"], atol=1e-9)

    def test_analytic_normal_rotates_with_the_source(self):
        data = gaussian()
        angle = np.pi / 5
        data["rotations"][0] = [np.cos(angle / 2), 0, np.sin(angle / 2), 0]
        direction = np.array([np.sin(angle), 0, np.cos(angle)])
        result = GaussianOctree(data).trace([direction], [-direction], 2)
        np.testing.assert_allclose(result["normals"][0], direction, atol=1e-6)

    def test_bad_inputs_and_cancellation_are_explicit(self):
        data = gaussian()
        data["scales"][0, 0] = 0
        with self.assertRaises(ValueError):
            GaussianOctree(data)
        tree = GaussianOctree(gaussian())
        with self.assertRaises(ValueError):
            tree.trace([[0, 0, 1]], [[0, 0, -2]], 2)
        with self.assertRaises(ValueError):
            tree.trace([[0, 0, 1]], [[0, 0, -1]], 2, density=float("nan"))

        def cancel():
            raise InterruptedError("cancelled")

        with self.assertRaises(InterruptedError):
            GaussianOctree(gaussian(), cancelled=cancel)
        tree.cancelled = cancel
        with self.assertRaises(InterruptedError):
            tree.trace([[0, 0, 1]], [[0, 0, -1]], 2)


class BakeTests(unittest.TestCase):
    def test_exported_tangents_and_reflected_geometry_keep_normal_green_axis(self):
        mesh = plane_mesh()
        mesh["positions"][..., 0] *= -1
        _p, n, t, sign, covered = rasterize_uv(mesh["positions"], mesh["normals"], mesh["uvs"], 64)
        self.assertTrue(np.all(sign[covered] == -1))
        detailed = np.array([0, 1, 1], np.float32) / np.sqrt(2)
        normal = encode_normal_map(np.broadcast_to(detailed, n.shape), t, n, sign)
        self.assertGreater(normal[32, 32, 1], 0.8)

        mesh["tangents"] = np.tile(np.array([1, 0, 0], np.float32), (2, 3, 1))
        mesh["tangent_signs"] = np.ones((2, 3), np.float32)
        _p, _n, explicit_t, explicit_sign, mask = rasterize_uv(
            mesh["positions"], mesh["normals"], mesh["uvs"], 64,
            corner_tangents=mesh["tangents"], tangent_signs=mesh["tangent_signs"])
        np.testing.assert_allclose(explicit_t[mask], np.tile([1, 0, 0], (int(mask.sum()), 1)))
        self.assertTrue(np.all(explicit_sign[mask] == 1))

    def test_rays_reach_displaced_source_that_point_sampling_cannot_reach(self):
        mesh = plane_mesh()
        mesh["positions"][:, :, 2] -= 0.05
        splats = plane_splats(count=8)
        with self.assertRaisesRegex(ValueError, "no valid texels"):
            project(mesh, splats, texture_size=64)
        result = project_octree(mesh, splats, texture_size=64, normal_smoothing=0)
        self.assertGreater(result.report["hits"], 3500)
        np.testing.assert_allclose(result.albedo[32, 32], [1, 0, 0], atol=1e-6)
        np.testing.assert_allclose(result.normal[32, 32], [0.5, 0.5, 1], atol=0.02)
        self.assertEqual(result.report["normal_source"], "GAUSSIAN_SURFACE_GRADIENT")

    def test_holes_remain_zero_confidence_even_when_color_is_filled(self):
        result = project_octree(plane_mesh(), plane_splats(mask=lambda p: p[:, 0] < 0.25),
                                texture_size=64, fill_holes=True)
        self.assertGreater(result.report["misses"], 1000)
        self.assertEqual(result.confidence[32, 60], 0)
        self.assertGreater(result.albedo[32, 60, 0], 0.9)

    def test_worker_has_a_distinct_octree_mode_and_writes_all_maps(self):
        with tempfile.TemporaryDirectory(prefix="remi-octree-worker-") as directory:
            root = Path(directory)
            np.savez(root / "mesh.npz", **plane_mesh())
            np.savez(root / "source.npz", **plane_splats(count=8))
            (root / "request.json").write_text(json.dumps({
                "mode": "octree", "mesh": str(root / "mesh.npz"), "archive": str(root / "source.npz"),
                "texture_size": 64, "normal_smoothing": 0,
            }))
            completed = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "integrations/splat_project/worker.py"),
                                        "--request", str(root / "request.json")], capture_output=True, text=True, timeout=120)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["method"], "OCTREE")
            if importlib.util.find_spec("numba") is not None:
                self.assertEqual(report["tracer_backend"], "NUMBA_CPU", report.get("acceleration_fallback"))
            self.assertGreater(report["hits"], 3500)
            for name in ("albedo.png", "normal.png", "confidence.png", "complete.json"):
                self.assertTrue((root / name).is_file(), name)

    def test_cancelled_worker_never_publishes_completion(self):
        with tempfile.TemporaryDirectory(prefix="remi-octree-cancel-") as directory:
            root = Path(directory)
            np.savez(root / "mesh.npz", **plane_mesh())
            np.savez(root / "source.npz", **plane_splats(count=8))
            (root / "cancel.request").touch()
            (root / "request.json").write_text(json.dumps({
                "mode": "octree", "mesh": str(root / "mesh.npz"), "archive": str(root / "source.npz"),
                "texture_size": 64,
            }))
            completed = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "integrations/splat_project/worker.py"),
                                        "--request", str(root / "request.json")], capture_output=True, text=True, timeout=120)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((root / "complete.json").exists())
            self.assertFalse((root / "normal.png").exists())


if __name__ == "__main__":
    unittest.main()
