"""Compiled ray-packet correctness and acceleration/fallback boundaries."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.splat_project.octree import GaussianOctree
from test_octree_projection import combine, gaussian

HAS_NUMBA = importlib.util.find_spec("numba") is not None


class FallbackTests(unittest.TestCase):
    def test_auto_uses_reference_when_numba_is_missing(self):
        tree = GaussianOctree(gaussian())
        args = ([[0, 0, 1]], [[0, 0, -1]], 2)
        expected = tree.trace(*args)
        with mock.patch.dict(sys.modules, {"integrations.splat_project.octree_native": None}):
            actual = tree.trace(*args, backend="AUTO")
        self.assertEqual(tree.trace_info["tracer_backend"], "NUMPY")
        self.assertIn("acceleration_fallback", tree.trace_info)
        for key in expected:
            np.testing.assert_array_equal(actual[key], expected[key])

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            GaussianOctree(gaussian()).trace([[0, 0, 1]], [[0, 0, -1]], 2, backend="invented")


@unittest.skipUnless(HAS_NUMBA, "Optional numba is not installed")
class NativeTests(unittest.TestCase):
    def compare(self, tree, origins, directions, lengths, **options):
        expected = tree.trace(origins, directions, lengths, **options, backend="NUMPY")
        actual = tree.trace(origins, directions, lengths, **options, backend="NUMBA_CPU")
        for key in ("hit", "clipped", "step_limit"):
            np.testing.assert_array_equal(actual[key], expected[key])
        for key in ("distance", "colors", "normals", "normal_agreement"):
            np.testing.assert_allclose(actual[key], expected[key], atol=2e-6, rtol=2e-6)
        self.assertEqual(tree.trace_info["tracer_backend"], "NUMBA_CPU")
        return actual

    def test_first_hit_parallel_boundary_misses_and_cage_clipping(self):
        tree = GaussianOctree(combine(gaussian(), gaussian((0.5, 0, -0.5), color=(0, 0, 1))), leaf_size=1)
        self.compare(tree, [[0, 0, 1], [0.5, 0, 1], [5, 5, 5], [0, 0, 0]],
                     [[0, 0, -1]] * 4, 2)

    def test_rotated_anisotropic_random_rays(self):
        rng = np.random.default_rng(542)
        data = combine(*(gaussian(tuple(rng.uniform(-0.5, 0.5, 3)), tuple(rng.uniform(0.03, 0.3, 3)),
                                  tuple(rng.random(3)), float(rng.uniform(0.01, 1))) for _ in range(96)))
        rotations = rng.normal(size=data["rotations"].shape)
        data["rotations"] = rotations / np.linalg.norm(rotations, axis=1, keepdims=True)
        origins = rng.uniform(-1, 1, (128, 3))
        directions = rng.normal(size=(128, 3))
        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
        self.compare(GaussianOctree(data, leaf_size=4), origins, directions, 3)

    def test_no_small_candidate_cap_drops_overlapping_gaussians(self):
        tree = GaussianOctree(combine(*(gaussian(opacity=0.01) for _ in range(320))))
        result = self.compare(tree, [[0, 0, 1]], [[0, 0, -1]], 2)
        self.assertTrue(result["hit"][0])

    def test_exhausted_steps_remain_misses(self):
        data = combine(*(gaussian((0, 0, float(z)), (0.2, 0.2, 0.03), opacity=0.01)
                         for z in np.linspace(-1, 1, 32)))
        result = self.compare(GaussianOctree(data), [[0, 0, 2]], [[0, 0, -1]], 4, max_steps=8)
        self.assertTrue(result["step_limit"][0])

    def test_thread_limit_is_restored_and_scratch_is_bounded(self):
        import numba
        previous = numba.get_num_threads()
        tree = GaussianOctree(gaussian())
        self.compare(tree, [[0, 0, 1]], [[0, 0, -1]], 2)
        self.assertEqual(numba.get_num_threads(), previous)
        self.assertLessEqual(tree.trace_info["tracer_scratch_bytes"], 128 * 1024**2)

    def test_auto_does_not_swallow_cancellation_or_memory_errors(self):
        for error in (InterruptedError("cancel"), MemoryError("budget")):
            tree = GaussianOctree(gaussian())
            with mock.patch("integrations.splat_project.octree_native.trace_native", side_effect=error), \
                    mock.patch.object(tree, "_trace_batch") as reference:
                with self.assertRaises(type(error)):
                    tree.trace([[0, 0, 1]], [[0, 0, -1]], 2, backend="AUTO")
                reference.assert_not_called()


if __name__ == "__main__":
    unittest.main()
