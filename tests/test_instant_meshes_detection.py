"""Feature recognition, negative controls and sampling without Blender."""

import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "remi_guide_geometry_tests"
if PACKAGE not in sys.modules:
    package = ModuleType(PACKAGE)
    package.__path__ = [str(ROOT / "features/retopology/instant_meshes")]
    sys.modules[PACKAGE] = package
detection = importlib.import_module(PACKAGE + ".detection")
guides = importlib.import_module(PACKAGE + ".guides")
quality = importlib.import_module(PACKAGE + ".quality")
from instant_meshes_fixtures import cylinder, washer, torus, sphere, plane


class DetectionTests(unittest.TestCase):
    def test_circular_boundaries_and_creases(self):
        for create, kind in ((washer, "BOUNDARY"), (cylinder, "CREASE")):
            vertices, faces = create()
            before = vertices.copy(), faces.copy()
            result = detection.detect_guides(vertices, faces, cylinders=False)
            self.assertEqual([g.kind for g in result.guides], [kind, kind])
            self.assertTrue(all(g.closed and g.confidence > 0.95 for g in result.guides))
            np.testing.assert_array_equal(vertices, before[0])
            np.testing.assert_array_equal(faces, before[1])

    def test_rotation_translation_scale_and_noise(self):
        vertices, faces = washer()
        rotation, _ = np.linalg.qr(np.random.default_rng(4).normal(size=(3, 3)))
        for scale in (1e-4, 1, 1e4):
            transformed = (vertices @ rotation) * scale + np.array([2, -4, 8]) * scale
            result = detection.detect_guides(transformed, faces, cylinders=False)
            self.assertEqual(len(result.guides), 2)
            np.testing.assert_allclose(sorted(g.metadata["radius"] / scale for g in result.guides),
                                       [0.5, 1.5], rtol=1e-5)
        noisy = vertices + np.random.default_rng(3).normal(0, 0.001, vertices.shape)
        self.assertEqual(len(detection.detect_guides(noisy, faces, cylinders=False).guides), 2)

    def test_ellipses_planes_arcs_and_branches_are_not_circles(self):
        vertices, faces = washer()
        vertices[:, 0] *= 1.7
        self.assertFalse(detection.detect_guides(vertices, faces, cylinders=False).guides)
        self.assertFalse(detection.detect_guides(*plane(), cylinders=False).guides)
        theta = np.linspace(0, np.pi, 48)
        self.assertIsNone(detection.fit_circle(np.column_stack((np.cos(theta), np.sin(theta), theta*0))))
        edges = np.array([[0, 1], [1, 2], [2, 0], [0, 3]])
        self.assertFalse(detection.closed_edge_loops(edges))

    def test_cylindrical_flow_is_local_and_does_not_label_spheres(self):
        result = detection.detect_guides(*cylinder(), target_scale=0.2)
        self.assertEqual(sum(g.kind == "CYLINDER" for g in result.guides), 3)
        self.assertFalse(detection.detect_guides(*sphere(), circles=False, curvature=False).guides)
        vertices, faces = cylinder()
        other, other_faces = cylinder()
        combined = np.vstack((vertices, other + [4, 0, 0]))
        combined_faces = np.vstack((faces, other_faces + len(vertices)))
        result = detection.detect_guides(combined, combined_faces, target_scale=0.2)
        self.assertEqual(sum(g.kind == "CYLINDER" for g in result.guides), 6)

    def test_curvature_requires_a_reliable_direction(self):
        options = dict(circles=False, cylinders=False, curvature=True, target_scale=0.1)
        self.assertFalse(detection.detect_guides(*plane(), **options).guides)
        self.assertFalse(detection.detect_guides(*sphere(), **options).guides)
        result = detection.detect_guides(*torus(), **options)
        self.assertGreater(len(result.guides), 4)
        self.assertTrue(all(g.kind == "CURVATURE" and g.confidence >= 0.55 for g in result.guides))

    def test_detection_limits_and_small_features(self):
        result = detection.detect_guides(*cylinder(), max_guides=2)
        self.assertEqual(len(result.guides), 2)
        self.assertTrue(result.notes)
        result = detection.detect_guides(*washer(), target_scale=10)
        self.assertFalse(result.guides)
        self.assertTrue(any("face budget" in note for note in result.notes))
        a = detection.detect_guides(*cylinder(), target_scale=0.2)
        b = detection.detect_guides(*cylinder(), target_scale=0.1)
        self.assertEqual([g.key for g in a.guides], [g.key for g in b.guides])

    def test_sampling_is_periodic_and_shape_preserving(self):
        theta = np.arange(64) * (2*np.pi / 64)
        points = np.column_stack((np.cos(theta), np.sin(theta), theta*0))
        sampled = guides.resample_path(np.vstack((points, points[:1])), 0.025, closed=True)
        self.assertGreater(len(sampled), len(points))
        self.assertLess(np.linalg.norm(sampled[0] - sampled[-1]), 0.026)
        self.assertGreater(np.linalg.norm(sampled[0] - sampled[-1]), 0)
        np.testing.assert_allclose(np.linalg.norm(sampled, axis=1), 1, atol=0.002)
        with self.assertRaises(ValueError):
            guides.resample_path(points, 1e-9, closed=True)
        with self.assertRaises(ValueError):
            guides.resample_path([[0, 0, 0], [0, 0, 0]], 1, closed=True)

    def test_topology_metrics_and_closed_loop_measurement(self):
        vertices, triangles = washer(segments=32, levels=5)
        # The fixture emits each quad as two adjacent triangles.
        quads = np.column_stack((triangles[::2, :2], triangles[::2, 2], triangles[1::2, 2]))
        measured = quality.mesh_quality(vertices, quads)
        self.assertEqual(measured["euler_characteristic"], 0)
        self.assertEqual(measured["components"], 1)
        self.assertEqual(measured["nonmanifold_edges"], 0)
        self.assertEqual(measured["quad_fraction"], 1)
        self.assertEqual(measured["degenerate_faces"], 0)
        detected = detection.detect_guides(vertices, triangles, cylinders=False)
        followed = quality.guide_quality(vertices, quads, detected.guides)
        self.assertTrue(all(item["closed_loop"] for item in followed))
        self.assertTrue(all(item["aligned_coverage"] == 1 for item in followed))
        # Removing a strip opens the cycles: coverage alone must not count as closure.
        broken = quality.guide_quality(vertices, quads[1:], detected.guides)
        self.assertTrue(any(not item["closed_loop"] for item in broken))
        strip_removed = quads[np.arange(len(quads)) % 32 != 0]
        self.assertEqual(quality.mesh_quality(vertices, strip_removed)["euler_characteristic"], 1)

    def test_quality_guard_does_not_reward_quads_alone(self):
        vertices, faces = plane(8)
        baseline = quality.mesh_quality(vertices, faces)
        worse = dict(baseline, quad_fraction=1, nonmanifold_edges=1)
        self.assertFalse(quality.prefer_guided(baseline, worse, [], [])[0])
        worse = dict(baseline, quad_fraction=1, euler_characteristic=7)
        self.assertFalse(quality.prefer_guided(baseline, worse, [], [])[0])
        worse = dict(baseline, angle_error_p95_deg=40)
        self.assertFalse(quality.prefer_guided(baseline, worse, [], [])[0])
        baseline.update(aspect_p95=2, angle_error_p95_deg=20, interior_poles=20)
        better = dict(baseline, aspect_p95=1.2, angle_error_p95_deg=8, interior_poles=10)
        self.assertTrue(quality.prefer_guided(baseline, better, [], [])[0])
        self.assertFalse(quality.prefer_guided(baseline, better,
                                             [{"closed_loop": True}], [{"closed_loop": False}])[0])


if __name__ == "__main__":
    unittest.main()
