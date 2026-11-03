"""Closed-loop shape preservation and constraint-priority regressions."""

import importlib.util
from pathlib import Path
import sys
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py",
                                                submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
import remi
from remi.features.retopology.instant_meshes._native import Session
from remi.features.retopology.instant_meshes.guides import FieldStroke, reproject_guide


def make_session(target=500):
    count = 41
    coordinates = np.linspace(-2, 2, count)
    vertices = np.asarray([(x, y, 0) for y in coordinates for x in coordinates], np.float32)
    faces = []
    for row in range(count - 1):
        for column in range(count - 1):
            a = row * count + column
            faces.extend(((a, a + 1, a + count + 1), (a, a + count + 1, a + count)))
    return Session(vertices, np.asarray(faces, np.int32), target_faces=target,
                   crease_angle=-1, align_boundaries=False, deterministic=True)


def circle(session):
    vertices, faces, normals = session.surface_snapshot()
    bvh = BVHTree.FromPolygons([Vector(v) for v in vertices], faces.tolist(), all_triangles=True)
    theta = np.linspace(0, 2 * np.pi, 64, endpoint=False)
    points = np.column_stack((np.cos(theta), np.sin(theta), np.zeros(len(theta)))).astype(np.float32)
    guide = FieldStroke(1, points, np.zeros_like(points), np.zeros(len(points), np.int32), closed=True)
    return reproject_guide(guide, bvh, session.average_edge_length * 0.5, 0.02)


session = make_session()
guide = circle(session)
session.set_strokes([1], [0, len(guide.positions)], guide.positions, guide.normals, guide.faces,
                    closed=[True], weights=[0.45], automatic=[True])
stored, _, _ = session.stroke_snapshot(0)
np.testing.assert_allclose(stored, guide.positions, atol=1e-6)
np.testing.assert_allclose(np.linalg.norm(stored[:, :2], axis=1), 1, atol=0.002)
directions, weights, anchors, anchor_weights = session.constraint_snapshot()
weights = weights.ravel()
assert np.allclose(weights[weights > 0], 0.45)
assert np.allclose(anchor_weights[anchor_weights > 0], 0.45)
assert session.constraint_snapshot(level=1)[1].max() <= 0.22501
assert session.constraint_snapshot(level=2)[1].max() <= 0.11251
session.set_strokes([1], [0, len(guide.positions)], guide.positions, guide.normals, guide.faces,
                    closed=[True], automatic=[True])
assert session.constraint_snapshot()[1].max() < 1, "Automatic guides must never become hard locks"

# A duplicated endpoint is accepted for old callers, without a seam tangent.
session.set_strokes([1], [0, len(guide.positions) + 1],
                    np.vstack((guide.positions, guide.positions[:1])),
                    np.vstack((guide.normals, guide.normals[:1])), np.r_[guide.faces, guide.faces[:1]])
assert len(session.stroke_snapshot(0)[0]) == len(guide.positions)
for invalid in (np.nan, 0, 1.01):
    try:
        session.set_strokes([1], [0, len(guide.positions)], guide.positions, guide.normals, guide.faces,
                            closed=[True], weights=[invalid])
    except RuntimeError:
        pass
    else:
        raise AssertionError("An invalid confidence was accepted")

# Manual directions win regardless of the caller's stroke ordering.
manual = guide.positions[:4].copy()
all_positions = np.vstack((manual, guide.positions))
all_normals = np.vstack((guide.normals[:4], guide.normals))
all_faces = np.r_[guide.faces[:4], guide.faces]
session.set_strokes([0, 1], [0, 4, len(all_positions)], all_positions, all_normals, all_faces,
                    closed=[False, True], weights=[1, 0.45], automatic=[False, True])
_, weights, _, anchor_weights = session.constraint_snapshot()
assert weights.max() == 1
assert np.any(anchor_weights == 0)

session.stop()
del session

# Reprojection must prefer a compatible sheet over a closer, opposite-facing
# surface. This is especially important for thin shells and rebuilt guides.
points = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], np.float32)
sheets = np.vstack((points, points + [0, 0, 0.005]))
sheet_faces = [[0, 1, 2], [0, 2, 3], [6, 5, 4], [7, 6, 4]]
tree = BVHTree.FromPolygons([Vector(p) for p in sheets], sheet_faces, all_triangles=True)
path = np.array([[-0.4, 0, 0.004], [0, 0, 0.004], [0.4, 0, 0.004]], np.float32)
stroke = FieldStroke(0, path, np.tile([0, 0, 1], (3, 1)), np.zeros(3, np.int32))
projected = reproject_guide(stroke, tree, 0.1, 0.01)
np.testing.assert_allclose(projected.positions[:, 2], 0, atol=1e-8)

remi.register()
from remi.features.retopology.instant_meshes.runtime import runtime
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3)
source = bpy.context.object
settings = bpy.context.scene.remi_instant_meshes
settings.target_faces = 500
settings.preserve_creases = False
runtime.start(source, settings)


def wait():
    deadline = time.monotonic() + 60
    while runtime.solve_stage or runtime.session.active:
        assert time.monotonic() < deadline
        runtime.poll()
        time.sleep(0.01)
    assert not runtime.last_error, runtime.last_error


wait()
theta = np.linspace(0, 2 * np.pi, 48, endpoint=False)
hits = []
for angle in theta:
    p, n, f, _ = runtime.surface_bvh.find_nearest(Vector((np.cos(angle), np.sin(angle), 0)))
    hits.append((p, n, f))
runtime.add_stroke(1, hits, closed=True)
wait()
before = runtime.strokes[0].positions.copy()
runtime.apply_target_faces(4000)
wait()
assert runtime.strokes[0].closed and runtime.preview is not None
for point, face in zip(runtime.strokes[0].positions, runtime.strokes[0].faces):
    triangle = runtime.surface_vertices[runtime.surface_faces[face]]
    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
    normal /= np.linalg.norm(normal)
    assert abs(np.dot(point - triangle[0], normal)) < 1e-5
runtime.shutdown()

# Suggestions can be reviewed independently of manual strokes, survive a
# hierarchy rebuild, and never create scene objects while previewing.
sys.path.insert(0, str(ROOT / "tests"))
from instant_meshes_fixtures import cylinder
vertices, faces = cylinder()
mesh = bpy.data.meshes.new("AutomaticGuidesSource")
mesh.from_pydata(vertices.tolist(), [], faces.tolist())
source = bpy.data.objects.new("AutomaticGuidesSource", mesh)
bpy.context.collection.objects.link(source)
settings.auto_guides = True
settings.auto_curvature = True
settings.target_faces = 1000
settings.deterministic = True
settings.preserve_creases = True
count_before = len(bpy.context.scene.objects)
runtime.start(source, settings)
wait()
assert len(runtime.automatic_guides) == 5, runtime.guide_notes
assert all(g.closed for g in runtime.automatic_guides)
assert len(bpy.context.scene.objects) == count_before
assert runtime.guidance_decision
assert runtime.baseline_preview is None
extracted = runtime.session.extract(strict=True)
np.testing.assert_allclose(extracted[0], runtime.preview[0], atol=1e-6)
np.testing.assert_array_equal(extracted[1], runtime.preview[1])
disabled_key = runtime.automatic_guides[0].key
runtime.toggle_automatic_guide(0)
wait()
assert not runtime.automatic_guides[0].enabled
assert disabled_key in runtime.disabled_guide_keys
disabled_cylinder_key = runtime.automatic_guides[-1].key
runtime.toggle_automatic_guide(len(runtime.automatic_guides) - 1)
wait()
runtime.apply_target_faces(5000)
wait()
assert any(g.key == disabled_key and not g.enabled for g in runtime.automatic_guides)
assert any(g.key == disabled_cylinder_key and not g.enabled for g in runtime.automatic_guides)
assert len(bpy.context.scene.objects) == count_before
for guide in runtime.automatic_guides:
    assert guide.faces.min() >= 0 and guide.faces.max() < len(runtime.surface_faces)
runtime.clear_automatic_guides()
wait()
assert not runtime.automatic_guides
runtime.detect_automatic_guides()
wait()
assert not any(g.enabled for g in runtime.automatic_guides)
runtime.detect_automatic_guides(reset=True)
wait()
assert all(g.enabled for g in runtime.automatic_guides)
guide = runtime.automatic_guides[-1]
runtime.add_stroke(0, [(guide.positions[i], guide.normals[i], guide.faces[i]) for i in (0, 5, 10, 15)])
wait()
assert len(runtime.strokes) == 1 and not runtime.guidance_decision
runtime.clear_automatic_guides()
wait()
assert len(runtime.strokes) == 1 and runtime.session.stroke_count == 1
runtime.undo_stroke()
wait()
runtime.detect_automatic_guides(reset=True)

# A failed guided extraction must recover the completed baseline, not strand
# the workspace without a preview or leave stale native fields behind.
deadline = time.monotonic() + 60
while runtime.guidance_pass != "GUIDED":
    assert time.monotonic() < deadline, runtime.last_error
    runtime.poll()
    time.sleep(0.01)


class FailOnce:
    def __init__(self, native):
        self.native = native
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.native, name)

    def extract(self, *args, **kwargs):
        if not self.failed:
            self.failed = True
            raise RuntimeError("injected guided extraction failure")
        return self.native.extract(*args, **kwargs)


runtime.session = FailOnce(runtime.session)
wait()
assert runtime.preview is not None and "guided candidate failed" in runtime.guidance_decision
assert runtime.session.position_solved and runtime.session.stroke_count == 0
np.testing.assert_allclose(runtime.session.extract()[0], runtime.preview[0], atol=1e-6)
runtime.shutdown()
remi.unregister()

# Blender supplies NumPy, so the release gate exercises the pure geometry suite
# without requiring it in the build machine's separate Python installation.
import unittest
import test_instant_meshes_detection
suite = unittest.defaultTestLoader.loadTestsFromModule(test_instant_meshes_detection)
assert unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
print("REMI_INSTANT_MESHES_GUIDES_OK")
