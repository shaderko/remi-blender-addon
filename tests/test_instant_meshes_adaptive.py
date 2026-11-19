"""Quad collapse and sampled shape-error guards without Blender or SciPy."""

import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "remi_adaptive_geometry_tests"
if PACKAGE not in sys.modules:
    package = ModuleType(PACKAGE)
    package.__path__ = [str(ROOT / "features/retopology/instant_meshes")]
    sys.modules[PACKAGE] = package
adaptive = importlib.import_module(PACKAGE+".adaptive")
quality = importlib.import_module(PACKAGE+".quality")


def grid(count=17, height=None):
    coordinates = np.linspace(-2, 2, count)
    vertices = np.array([(x, y, 0 if height is None else height(x, y))
                         for y in coordinates for x in coordinates], np.float32)
    faces = []
    for row in range(count-1):
        for column in range(count-1):
            a = row*count+column
            faces.append([a, a+1, a+count+1, a+count])
    return vertices, np.array(faces, np.int32)


class Plane:
    def nearest(self, point, normal, limit):
        return np.array([point[0], point[1], 0.0]), np.array([0.0, 0.0, 1.0]), abs(point[2])


class HeightSurface:
    @staticmethod
    def height(x, y):
        return .16*max(0, x)**2*(1+.3*np.sin(y*5))

    def nearest(self, point, normal, limit):
        x, y = point[:2]
        z = self.height(x, y)
        dx = .32*max(0, x)*(1+.3*np.sin(y*5))
        dy = .24*max(0, x)**2*np.cos(y*5)
        n = np.array([-dx, -dy, 1.0])
        return np.array([x, y, z]), n/np.linalg.norm(n), abs(z-point[2])


def simplify(vertices, faces, ratio=.5, error=.01, surface=None, **options):
    task = adaptive.QuadSimplifier(vertices, faces, surface or Plane(),
                                  target_faces=int(len(faces)*ratio), error=error, **options)
    for _ in task.run():
        pass
    return task


class AdaptiveTests(unittest.TestCase):
    def test_flat_patch_reduction_keeps_quads_boundaries_and_input(self):
        vertices, faces = grid()
        original = vertices.copy(), faces.copy()
        task = simplify(vertices, faces)
        print("ADAPTIVE_PLANE", task.report)
        self.assertLess(len(task.result[1]), len(faces)*.8)
        before, after = quality.mesh_quality(vertices, faces), quality.mesh_quality(*task.result)
        for key in ("components", "euler_characteristic", "boundary_edges", "nonmanifold_edges"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(after["quad_fraction"], 1)
        self.assertEqual(after["degenerate_faces"], 0)
        self.assertLessEqual(task.report["sampled_patch_error_max"], .01)
        boundary = vertices[(np.abs(vertices[:, 0]) == 2) | (np.abs(vertices[:, 1]) == 2)]
        for point in boundary:
            self.assertTrue(np.any(np.all(task.result[0] == point, axis=1)))
        np.testing.assert_array_equal(vertices, original[0])
        np.testing.assert_array_equal(faces, original[1])

    def test_full_budget_is_exact_identity_and_repeated_runs_match(self):
        vertices, faces = grid(11)
        full = simplify(vertices, faces, ratio=1)
        np.testing.assert_array_equal(full.result[0], vertices)
        np.testing.assert_array_equal(full.result[1], faces)
        first, second = simplify(vertices, faces), simplify(vertices, faces)
        np.testing.assert_array_equal(first.result[0], second.result[0])
        np.testing.assert_array_equal(first.result[1], second.result[1])

    def test_curved_region_retains_more_density_than_flat_region(self):
        surface = HeightSurface()
        vertices, faces = grid(21, surface.height)
        task = simplify(vertices, faces, ratio=.5, error=.006, surface=surface)
        centers = task.result[0][task.result[1]].mean(axis=1)
        flat, curved = np.sum(centers[:, 0] < -.3), np.sum(centers[:, 0] > .3)
        print("ADAPTIVE_CURVATURE", flat, curved, task.report)
        self.assertLess(len(task.result[1]), len(faces))
        self.assertGreater(curved, flat)
        self.assertLessEqual(task.report["sampled_patch_error_max"], .006+1e-8)

    def test_invalid_mixed_and_nonmanifold_inputs_are_not_repaired_silently(self):
        vertices, faces = grid(7)
        for invalid in (faces[:, :3], np.vstack((faces, faces[:1])),
                        np.vstack((faces, faces[:1], faces[:1]))):
            task = simplify(vertices, invalid)
            np.testing.assert_array_equal(task.result[0], vertices)
            np.testing.assert_array_equal(task.result[1], invalid)
            self.assertEqual(task.report["collapses"], 0)
        for error in (0, -1, np.nan, np.inf):
            with self.assertRaises(ValueError):
                simplify(vertices, faces, error=error)

    def test_triangle_distance_samples_interiors_and_not_only_vertices(self):
        triangle = np.array([[[0, 0, 0], [2, 0, 0], [0, 2, 0]]], float)
        points = np.array([[.5, .5, 1], [3, 0, 0], [.5, .5, 0]])
        np.testing.assert_allclose(adaptive.triangle_distances(points, triangle)[:, 0], [1, 1, 0])

    def test_historical_witnesses_are_not_lost_as_reductions_accumulate(self):
        vertices, faces = grid(11)
        task = adaptive.QuadSimplifier(vertices, faces, Plane(), target_faces=25, error=.01)
        count = 0
        for _ in task.run():
            if hasattr(task, "witnesses"):
                current = sum(len(items) for items in task.witnesses.values())
                self.assertGreaterEqual(current, count)
                self.assertLessEqual(current, len(faces)*len(adaptive.SAMPLE_WEIGHTS))
                count = current
        self.assertGreater(task.report["collapses"], 0)

    def test_bow_tie_vertex_fans_are_rejected(self):
        vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                             [-1, 0, 0], [-1, -1, 0], [0, -1, 0],
                             [4, 0, 0], [5, 0, 0], [5, 1, 0], [4, 1, 0],
                             [6, 0, 0], [7, 0, 0], [7, 1, 0], [6, 1, 0],
                             [8, 0, 0], [9, 0, 0], [9, 1, 0], [8, 1, 0]], np.float32)
        faces = np.array([[0, 1, 2, 3], [0, 4, 5, 6], [7, 8, 9, 10],
                          [11, 12, 13, 14], [15, 16, 17, 18]], np.int32)
        task = simplify(vertices, faces, ratio=.5)
        self.assertIn("vertex fan", task.report["reason"])
        np.testing.assert_array_equal(task.result[1], faces)


if __name__ == "__main__":
    unittest.main()
