"""Independent source patches: density contrast, conformity and error guards."""

import importlib
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

from test_instant_meshes_adaptive import PACKAGE, grid, HeightSurface, Plane

surface_adaptive = importlib.import_module(PACKAGE+".surface_adaptive")
quality = importlib.import_module(PACKAGE+".quality")


def triangulate(faces):
    return np.stack((faces[:, [0, 1, 2]], faces[:, [0, 2, 3]]), axis=1).reshape(-1, 3)


def rebuild(vertices, faces, *, surface=None, error=.01, fallback=None, **options):
    task = surface_adaptive.SurfacePatchRemesher(vertices, triangulate(faces), surface or Plane(),
        fallback=fallback or (vertices, faces), error=error, **options)
    for _ in task.run():
        pass
    return task


class SurfaceAdaptiveTests(unittest.TestCase):
    def test_flat_region_discards_entire_native_grid(self):
        vertices, faces = grid(25)
        original = vertices.copy(), faces.copy()
        task = rebuild(vertices, faces)
        self.assertTrue(task.report["rebuilt"], task.report)
        self.assertEqual(task.report["patches"], 1)
        self.assertEqual(len(task.result[1]), 1)
        self.assertEqual(len(task.result[0]), 4)
        self.assertEqual(quality.mesh_quality(*task.result)["quad_fraction"], 1)
        self.assertEqual(quality.mesh_quality(*task.result)["interior_poles"], 0)
        self.assertLess(task.report["sampled_patch_error_max"], 1e-6)
        np.testing.assert_array_equal(vertices, original[0])
        np.testing.assert_array_equal(faces, original[1])

    def test_native_seed_density_does_not_determine_rebuild(self):
        vertices, faces = grid(17)
        first = rebuild(vertices, faces)
        other_seed = grid(7)
        second = rebuild(vertices, faces, fallback=other_seed)
        self.assertTrue(first.report["rebuilt"] and second.report["rebuilt"])
        np.testing.assert_array_equal(first.result[0], second.result[0])
        np.testing.assert_array_equal(first.result[1], second.result[1])

    def test_already_minimal_source_is_not_subdivided_like_the_native_grid(self):
        vertices, faces = grid(2)
        task = rebuild(vertices, faces, fallback=grid(17))
        self.assertTrue(task.report["rebuilt"], task.report)
        self.assertEqual(len(task.result[1]), 1)

    def test_closed_cube_retains_only_its_six_source_quads(self):
        vertices = np.array([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],
                             [-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]], np.float32)
        faces = np.array([[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]], np.int32)

        class Box:
            def nearest(self, point, normal, limit):
                axis = int(np.abs(normal).argmax())
                n = np.zeros(3)
                n[axis] = np.sign(normal[axis])
                location = np.array(point, copy=True)
                location[axis] = n[axis]
                return location, n, abs(point[axis]-n[axis])

        task = rebuild(vertices, faces, surface=Box(), fallback=grid(17), crease_angle=35)
        self.assertTrue(task.report["rebuilt"], task.report)
        self.assertEqual(len(task.result[1]), 6)
        self.assertEqual(len(task.result[0]), 8)
        self.assertEqual(quality.mesh_quality(*task.result)["boundary_edges"], 0)

    def test_strong_detail_to_smooth_density_contrast_without_cracks(self):
        surface = HeightSurface()
        vertices, faces = grid(49, surface.height)
        task = rebuild(vertices, faces, surface=surface, error=.02, planar_only=True)
        print("REMI_SURFACE_DETAIL", task.report)
        self.assertTrue(task.report["rebuilt"], task.report)
        centers = task.result[0][task.result[1]].mean(axis=1)
        smooth, detail = np.sum(centers[:, 0] < -.4), np.sum(centers[:, 0] > .4)
        self.assertGreater(detail, max(1, smooth)*16)
        self.assertLess(task.report["sampled_patch_error_max"], .02+1e-7)
        before, after = quality.mesh_quality(vertices, faces), quality.mesh_quality(*task.result)
        for key in ("components", "euler_characteristic", "nonmanifold_edges", "degenerate_faces"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(after["quad_fraction"], 1)
        self.assertLess(len(task.result[1]), len(faces)*.6)

    def test_small_displacements_do_not_force_geometry_density(self):
        class FineDetail:
            def height(self, x, y):
                return .001*np.sin(x*8)*np.sin(y*8)

            def nearest(self, point, normal, limit):
                x, y = point[:2]
                n = np.array([-.008*np.cos(x*8)*np.sin(y*8), -.008*np.sin(x*8)*np.cos(y*8), 1.0])
                return np.array([x, y, self.height(x, y)]), n/np.linalg.norm(n), abs(point[2]-self.height(x, y))

        detail = FineDetail()
        vertices, faces = grid(33, detail.height)
        task = rebuild(vertices, faces, surface=detail, error=.02, planar_only=True)
        self.assertTrue(task.report["rebuilt"], task.report)
        self.assertEqual(len(task.result[1]), 1)
        self.assertLessEqual(task.report["sampled_patch_error_max"], .02)

    def test_real_creases_survive_even_at_a_very_coarse_detail_error(self):
        class Fold:
            def nearest(self, point, normal, limit):
                x, y = point[:2]
                n = np.array([-.25*np.sign(x), 0.0, 1.0])
                return np.array([x, y, .25*abs(x)]), n/np.linalg.norm(n), abs(point[2]-.25*abs(x))

        vertices, faces = grid(17, lambda x, y: .25*abs(x))
        task = rebuild(vertices, faces, surface=Fold(), error=.8, crease_angle=20, planar_only=True)
        self.assertTrue(task.report["rebuilt"], task.report)
        self.assertEqual(len(task.result[1]), 2)
        self.assertEqual(task.report["patches"], 2)
        centers = task.result[0][task.result[1]].mean(axis=1)
        self.assertTrue(np.any(centers[:, 0] < 0) and np.any(centers[:, 0] > 0))

    def test_guides_are_not_silently_ignored_without_native_solver(self):
        vertices, faces = grid(7)
        # This is the no-native fallback contract. CI builds the extension before
        # running pure-Python tests, so model that boundary explicitly rather
        # than accidentally entering Blender's mathutils-dependent solver.
        with patch.dict(sys.modules, {PACKAGE+"._native": None}):
            task = rebuild(vertices, faces, guides=[SimpleNamespace(enabled=True)])
        self.assertFalse(task.report["rebuilt"])
        self.assertIn("Seed Simplify", task.report["reason"])
        np.testing.assert_array_equal(task.result[0], vertices)
        np.testing.assert_array_equal(task.result[1], faces)

    def test_transition_parity_only_splits_necessary_edges(self):
        cells = [[0, 1, 4, 3], [1, 2, 4], [2, 5, 4]]
        split = surface_adaptive._transition_edges(cells)
        self.assertIn((1, 4), split)
        for cell in cells:
            count = sum(tuple(sorted(edge)) in split for edge in zip(cell, cell[1:]+cell[:1]))
            self.assertEqual(count % 2, len(cell) % 2)
        self.assertEqual(surface_adaptive._transition_edges([[0, 1, 2, 3]]), set())

    def test_timeout_never_exposes_a_partial_rebuild(self):
        vertices, faces = grid(17)
        task = rebuild(vertices, faces, max_seconds=1e-9)
        self.assertFalse(task.report["rebuilt"])
        self.assertIn("Time budget", task.report["reason"])
        np.testing.assert_array_equal(task.result[0], vertices)
        np.testing.assert_array_equal(task.result[1], faces)

    def test_closed_sheets_touching_at_one_vertex_are_rejected(self):
        vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                             [-1, 0, 0], [0, -1, 0], [0, 0, -1]], np.float32)
        tetra = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], np.int32)
        triangles = np.vstack((tetra, np.array([0, 4, 5, 6])[tetra]))
        fallback = grid(7)
        task = surface_adaptive.SurfacePatchRemesher(vertices, triangles, Plane(), fallback=fallback, error=.1, planar_only=True)
        for _ in task.run():
            pass
        self.assertFalse(task.report["rebuilt"])
        self.assertIn("vertex fan", task.report["reason"])
        np.testing.assert_array_equal(task.result[1], fallback[1])

    def test_subdivision_without_actual_coarsening_is_not_reported_as_success(self):
        vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
        triangles = np.array([[0, 1, 2]], np.int32)
        fallback = grid(7)
        task = surface_adaptive.SurfacePatchRemesher(vertices, triangles, Plane(), fallback=fallback, error=.01)
        for _ in task.run():
            pass
        self.assertFalse(task.report["rebuilt"])
        self.assertIn("not coarsen", task.report["reason"])
        self.assertEqual(task.report["candidate_faces"], 3)
        np.testing.assert_array_equal(task.result[1], fallback[1])

    def test_parts_are_not_welded_even_when_coordinates_overlap(self):
        vertices, faces = grid(9)
        doubled = np.vstack((vertices, vertices)), np.vstack((faces, faces+len(vertices)))
        task = rebuild(*doubled)
        self.assertTrue(task.report["rebuilt"], task.report)
        self.assertEqual(quality.mesh_quality(*task.result)["components"], 2)
        self.assertEqual(len(task.result[1]), 2)

    def test_unsafe_source_retains_exact_native_fallback(self):
        vertices, faces = grid(7)
        invalid = triangulate(faces)
        invalid[0] = invalid[0, ::-1]
        task = surface_adaptive.SurfacePatchRemesher(vertices, invalid, Plane(),
            fallback=(vertices, faces), error=.01)
        for _ in task.run():
            pass
        self.assertFalse(task.report["rebuilt"])
        self.assertIn("winding", task.report["reason"])
        np.testing.assert_array_equal(task.result[0], vertices)
        np.testing.assert_array_equal(task.result[1], faces)


if __name__ == "__main__":
    unittest.main()
