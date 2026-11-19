"""Native integration, protected guides/creases, fallback and source isolation."""

import json
from pathlib import Path
import sys
import time
from unittest import mock

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
exec(compile((ROOT/"tests/blender_checkout_bootstrap.py").read_text(),
             str(ROOT/"tests/blender_checkout_bootstrap.py"), "exec"))
import remi
from remi.features.retopology.instant_meshes import adaptive
from remi.features.retopology.instant_meshes.guides import FieldStroke
from remi.features.retopology.instant_meshes.quality import guide_quality, mesh_quality
from remi.features.retopology.instant_meshes.runtime import runtime

sys.path.insert(0, str(ROOT/"tests"))
from test_instant_meshes_adaptive import grid, HeightSurface


def tree(vertices, faces):
    return BVHTree.FromPolygons([Vector(v) for v in vertices], faces.tolist())


def run_task(vertices, faces, *, guides=(), crease_angle=35, ratio=.5, error=.01):
    task = adaptive.QuadSimplifier(vertices, faces, adaptive.BVHSurface(tree(vertices, faces)),
                                  target_faces=int(len(faces)*ratio), error=error, guides=guides,
                                  crease_angle=crease_angle)
    for _ in task.run():
        pass
    return task


def wait():
    deadline = time.monotonic()+60
    while runtime.solve_stage or runtime.busy:
        assert time.monotonic() < deadline, runtime.last_error
        runtime.poll()
        time.sleep(.001)
    assert not runtime.last_error, runtime.last_error


# Guides are tubes around actual segments, not only sparse endpoint samples.
vertices, faces = grid(17)
guide_points = np.array([[0, -2, 0], [0, 2, 0]], np.float32)
guide = FieldStroke(1, guide_points, np.tile([0, 0, 1], (2, 1)), np.zeros(2, np.int32))
guided = run_task(vertices, faces, guides=[guide])
assert guided.report["collapses"] > 0
for point in vertices[np.abs(vertices[:, 0]) < 1e-8]:
    assert np.any(np.all(guided.result[0] == point, axis=1)), "Guide vertex moved/was removed"

# Exact closed cycles that already exist must survive the protection pass.
segments, levels = 32, 9
theta = np.arange(segments)*2*np.pi/segments
annulus = np.array([(r*np.cos(t), r*np.sin(t), 0) for r in np.linspace(.5, 1.5, levels) for t in theta], np.float32)
quads = np.array([[j*segments+i, j*segments+(i+1) % segments,
                   (j+1)*segments+(i+1) % segments, (j+1)*segments+i]
                  for j in range(levels-1) for i in range(segments)], np.int32)
points = annulus[4*segments:5*segments]
loop = FieldStroke(1, points.copy(), np.tile([0, 0, -1], (segments, 1)), np.zeros(segments, np.int32),
                   closed=True, kind="MANUAL", metadata={"center": [0, 0, 0], "axis": [0, 0, 1], "radius": 1})
closed = run_task(annulus, quads, guides=[loop], error=.02)
assert closed.report["collapses"] > 0
assert guide_quality(*closed.result, [loop])[0]["closed_loop"]
for point in points:
    assert np.any(np.all(closed.result[0] == point, axis=1)), "Protected cycle was changed"

# Sharp features are protected even when no guide has been drawn.
folded, quads = grid(17, lambda x, y: .5*abs(x))
sharp = run_task(folded, quads, crease_angle=20)
assert sharp.report["collapses"] > 0
for point in folded[np.abs(folded[:, 0]) < 1e-8]:
    assert np.any(np.all(sharp.result[0] == point, axis=1)), "A sharp crease was simplified away"

# Reprojection must not jump to a closer, opposite-facing thin sheet.
sheet = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]], np.float32)
sheets = np.vstack((sheet, sheet+[0, 0, .005]))
source = adaptive.BVHSurface(tree(sheets, np.array([[0, 1, 2, 3], [7, 6, 5, 4]], np.int32)))
hit = source.nearest(np.array([0, 0, .004]), np.array([0, 0, 1]), .01)
assert hit is not None and abs(hit[0][2]) < 1e-8

remi.register()
try:
    settings = bpy.context.scene.remi_instant_meshes
    assert not settings.adaptive_quads
    settings.target_faces = 1200
    settings.preserve_creases = False
    settings.deterministic = True
    vertices, faces = grid(33, HeightSurface.height)
    mesh = bpy.data.meshes.new("Adaptive source mesh")
    mesh.from_pydata(vertices.tolist(), [], faces.tolist())
    obj = bpy.data.objects.new("Adaptive source", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    object_count = len(bpy.context.scene.objects)
    runtime.start(obj, settings)
    wait()
    uniform = tuple(value.copy() for value in runtime.preview)
    np.testing.assert_allclose(runtime.session.extract()[0], uniform[0], atol=1e-6)
    settings.adaptive_quads = True
    settings.adaptive_method = "SIMPLIFY"
    settings.adaptive_budget = "0.5"
    settings.adaptive_error = .003
    runtime.update_preview()
    assert runtime.busy and runtime.solve_stage == "ADAPTIVE"
    assert not bpy.ops.remi.instant_meshes_accept.poll(), "An incomplete adaptive preview could be accepted"
    assert bpy.ops.remi.instant_meshes_cancel.poll(), "The adaptive pass cannot be cancelled"
    wait()
    assert runtime.adaptive_report["collapses"] > 0, runtime.adaptive_report
    assert len(runtime.preview[1]) < len(uniform[1])
    assert runtime.preview_quality["quad_fraction"] == 1
    assert not runtime.preview_quality["nonmanifold_edges"]
    assert runtime.preview_normals.shape == runtime.preview[0].shape
    assert np.isfinite(runtime.preview_normals).all()
    assert len(bpy.context.scene.objects) == object_count
    np.testing.assert_array_equal(np.array([v.co[:] for v in obj.data.vertices], np.float32), vertices)
    native = runtime.session.extract()
    np.testing.assert_allclose(native[0], uniform[0], atol=1e-6)
    np.testing.assert_array_equal(native[1], uniform[1])
    print("REMI_ADAPTIVE_NATIVE_REPORT", json.dumps(runtime.adaptive_report), flush=True)

    # Full/native is reversible; reductions never accumulate on a prior result.
    settings.adaptive_budget = "1.0"
    runtime.update_preview()
    wait()
    np.testing.assert_allclose(runtime.preview[0], uniform[0], atol=1e-6)
    np.testing.assert_array_equal(runtime.preview[1], uniform[1])

    def fail(self):
        yield
        raise RuntimeError("injected adaptive failure")

    settings.adaptive_budget = "0.5"
    with mock.patch.object(adaptive.QuadSimplifier, "run", fail):
        runtime.update_preview()
        wait()
    assert "injected adaptive failure" in runtime.adaptive_report["reason"]
    np.testing.assert_array_equal(runtime.preview[1], uniform[1])

    runtime.update_preview()
    wait()
    reduced_count = len(runtime.preview[1])
    assert bpy.ops.remi.instant_meshes_accept() == {"FINISHED"}
    result = bpy.context.active_object
    assert len(result.data.polygons) == reduced_count
    assert all(len(face.vertices) == 4 for face in result.data.polygons)
    np.testing.assert_array_equal(np.array([v.co[:] for v in obj.data.vertices], np.float32), vertices)
    runtime.start(obj, settings)
    deadline = time.monotonic()+60
    while runtime.solve_stage != "ADAPTIVE":
        assert time.monotonic() < deadline
        runtime.poll()
        assert not runtime.last_error
        time.sleep(.001)
    while runtime.adaptive_task.report["collapses"] < 3:
        assert time.monotonic() < deadline
        runtime.poll()
        assert runtime.adaptive_task is not None
    runtime.shutdown()
    assert runtime.adaptive_task is None and runtime.session is None
    np.testing.assert_array_equal(np.array([v.co[:] for v in obj.data.vertices], np.float32), vertices)
finally:
    runtime.shutdown()
    remi.unregister()

import unittest
import test_instant_meshes_adaptive
suite = unittest.defaultTestLoader.loadTestsFromModule(test_instant_meshes_adaptive)
assert unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
print("REMI_INSTANT_MESHES_ADAPTIVE_REGRESSION_OK", flush=True)
