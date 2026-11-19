"""Render-seam recovery must not become indiscriminate coordinate welding."""

import importlib
import unittest

import numpy as np

from test_instant_meshes_adaptive import PACKAGE, grid

topology = importlib.import_module(PACKAGE+".surface_topology")
quality = importlib.import_module(PACKAGE+".quality")


def prepare(vertices, faces):
    task = topology.stitch_render_seams(vertices, faces)
    while True:
        try:
            next(task)
        except StopIteration as result:
            return result.value


def triangles(quads):
    return np.stack((quads[:, [0,1,2]], quads[:, [0,2,3]]), axis=1).reshape(-1, 3)


class RenderSeamTests(unittest.TestCase):
    def test_inconsistent_shared_edge_orientation_is_rejected(self):
        faces = np.array([[0,1,2],[0,2,3]], np.int32)
        self.assertTrue(topology.consistent_winding(faces))
        faces[1] = faces[1, ::-1]
        self.assertFalse(topology.consistent_winding(faces))

    def test_triangle_soup_becomes_connected_without_moving_or_deleting_faces(self):
        vertices, quads = grid(7)
        faces = triangles(quads)
        split_vertices = vertices[faces].reshape(-1, 3).copy()
        split_faces = np.arange(len(split_vertices), dtype=np.int32).reshape(-1, 3)
        original = split_vertices.copy(), split_faces.copy()
        v, f, report = prepare(split_vertices, split_faces)
        self.assertEqual(len(v), len(vertices))
        self.assertEqual(quality.mesh_quality(v, f)["components"], 1)
        self.assertGreater(report["seam_edge_pairs"], 0)
        np.testing.assert_array_equal(v[f], split_vertices[split_faces])
        np.testing.assert_array_equal(split_vertices, original[0])
        np.testing.assert_array_equal(split_faces, original[1])

    def test_closed_overlapping_parts_stay_separate(self):
        vertices = np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]], np.float32)
        faces = np.array([[0,2,1],[0,1,3],[1,2,3],[2,0,3]], np.int32)
        v, f, report = prepare(np.vstack((vertices, vertices)), np.vstack((faces, faces+4)))
        self.assertEqual(len(v), 8)
        self.assertEqual(quality.mesh_quality(v, f)["components"], 2)
        self.assertEqual(report["seam_edge_pairs"], 0)

    def test_duplicate_opposite_facing_sheets_are_not_stitched(self):
        vertices, quads = grid(2)
        faces = triangles(quads)
        v, f, report = prepare(np.vstack((vertices, vertices)), np.vstack((faces, faces[:, ::-1]+4)))
        self.assertEqual(len(v), 8)
        self.assertEqual(quality.mesh_quality(v, f)["components"], 2)
        self.assertEqual(report["seam_edge_pairs"], 0)
        self.assertEqual(report["duplicate_geometric_faces"], 4)

    def test_nearby_edges_are_not_closed_by_a_weld_tolerance(self):
        vertices = np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1e-6],[1,0,1e-6],[0,1,1e-6]], np.float32)
        faces = np.array([[0,1,2],[3,5,4]], np.int32)
        v, f, report = prepare(vertices, faces)
        np.testing.assert_array_equal(v, vertices)
        np.testing.assert_array_equal(f, faces)
        self.assertEqual(report["seam_edge_pairs"], 0)

    def test_ambiguous_nonmanifold_render_edge_is_reported_not_welded(self):
        vertices = np.array([[0,0,0],[1,0,0],[0,1,0],[0,-1,0],[0,0,1]], np.float32)
        triangles = np.array([[0,1,2],[1,0,3],[0,1,4]], np.int32)
        split = vertices[triangles].reshape(-1, 3)
        faces = np.arange(len(split), dtype=np.int32).reshape(-1, 3)
        output, result, report = prepare(split, faces)
        self.assertEqual(report["ambiguous_render_edges"], 1)
        self.assertEqual(report["seam_edge_pairs"], 0)
        np.testing.assert_array_equal(output, split)
        np.testing.assert_array_equal(result, faces)


if __name__ == "__main__":
    unittest.main()
