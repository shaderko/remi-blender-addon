"""Source rebuild in the native runtime, default-off, cancellation and restore."""

from pathlib import Path
import sys
import time

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
exec(compile((ROOT / "tests/blender_checkout_bootstrap.py").read_text(),
             str(ROOT / "tests/blender_checkout_bootstrap.py"), "exec"))
import remi
from remi.features.retopology.instant_meshes.runtime import runtime

sys.path.insert(0, str(ROOT / "tests"))
from test_instant_meshes_adaptive import grid
from remi.features.retopology.instant_meshes.guides import FieldStroke


def wait():
    deadline = time.monotonic()+60
    while runtime.solve_stage or runtime.busy:
        assert time.monotonic() < deadline, runtime.last_error
        runtime.poll()
        time.sleep(.001)
    assert not runtime.last_error, runtime.last_error


remi.register()
try:
    settings = bpy.context.scene.remi_instant_meshes
    assert not settings.adaptive_quads and settings.adaptive_method == "SURFACE"
    settings.target_faces = 1200
    settings.deterministic = True
    settings.preserve_creases = False
    vertices, faces = grid(33)
    mesh = bpy.data.meshes.new("Surface rebuild source")
    mesh.from_pydata(vertices.tolist(), [], faces.tolist())
    obj = bpy.data.objects.new("Surface rebuild source", mesh)
    hidden = bpy.data.collections.new("Excluded GLB source collection")
    bpy.context.scene.collection.children.link(hidden)
    hidden.objects.link(obj)
    hidden.hide_viewport = True
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    source_count = len(bpy.context.scene.objects)
    runtime.start(obj, settings)
    wait()
    native = tuple(values.copy() for values in runtime.preview)
    assert len(native[1]) > 500
    settings.adaptive_quads = True
    runtime.update_preview()
    assert runtime.busy and not bpy.ops.remi.instant_meshes_accept.poll()
    wait()
    assert runtime.adaptive_report["rebuilt"], runtime.adaptive_report
    assert len(runtime.preview[1]) == 1 and len(runtime.preview[0]) == 4
    assert runtime.preview_normals.shape == runtime.preview[0].shape
    assert np.isfinite(runtime.preview_normals).all()
    first = tuple(values.copy() for values in runtime.preview)
    settings.target_faces = 600
    runtime.apply_target_faces(600)
    wait()
    for a, b in zip(first, runtime.preview):
        np.testing.assert_array_equal(a, b)
    # Normal IM remains exactly the native extraction, not a rebuild/undo guess.
    settings.adaptive_quads = False
    runtime.update_preview()
    assert not runtime.busy
    extracted = runtime.session.extract()
    np.testing.assert_allclose(runtime.preview[0], extracted[0], atol=1e-6)
    np.testing.assert_array_equal(runtime.preview[1], extracted[1])
    assert len(runtime.preview[1]) > 300
    # A rejected rebuild is never offered as successful adaptive output.
    settings.adaptive_quads = True
    settings.adaptive_error = 1e-6
    settings.adaptive_face_limit = 1000
    runtime.strokes = [FieldStroke(0, np.array([[-1,0,0],[1,0,0]], np.float32),
                                  np.array([[0,0,1],[0,0,1]], np.float32), np.array([0,1], np.int32))]
    runtime.update_preview()
    wait()
    assert runtime.adaptive_failed and not runtime.can_accept
    assert "NOT APPLIED" in settings.status, settings.status
    assert not bpy.ops.remi.instant_meshes_accept.poll()
    assert not bpy.ops.remi.instant_meshes_session_accept.poll()
    from remi.features.retopology.instant_meshes.operators import _create_preview_object
    try:
        _create_preview_object(bpy.context)
        raise AssertionError("A failed Surface Rebuild was accepted")
    except RuntimeError as error:
        assert "not applied" in str(error)
    assert len(bpy.context.scene.objects) == source_count
    runtime.strokes = []
    assert bpy.ops.remi.instant_meshes_use_native() == {"FINISHED"}
    assert not settings.adaptive_quads and runtime.can_accept
    assert not runtime.adaptive_report
    settings.adaptive_error = .02
    settings.adaptive_face_limit = 25000
    settings.adaptive_quads = True
    runtime.update_preview()
    assert bpy.ops.remi.instant_meshes_cancel.poll()
    runtime.poll()
    runtime.shutdown()
    assert runtime.adaptive_task is None and runtime.adaptive_iterator is None
    np.testing.assert_array_equal(np.array([vertex.co[:] for vertex in obj.data.vertices], np.float32), vertices)
    assert len(bpy.context.scene.objects) == source_count
    runtime.start(obj, settings)
    wait()
    assert runtime.adaptive_report["rebuilt"] and len(runtime.preview[1]) == 1
    assert bpy.ops.remi.instant_meshes_accept() == {"FINISHED"}
    accepted = bpy.context.active_object
    assert accepted != obj and len(accepted.data.vertices) == 4 and len(accepted.data.polygons) == 1
    assert len(accepted.data.polygons[0].vertices) == 4
    np.testing.assert_array_equal(np.array([vertex.co[:] for vertex in obj.data.vertices], np.float32), vertices)
finally:
    runtime.shutdown()
    remi.unregister()

import unittest
import test_surface_adaptive
assert unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_surface_adaptive)).wasSuccessful()
print("REMI_SOURCE_SURFACE_ADAPTIVE_REGRESSION_OK", flush=True)
