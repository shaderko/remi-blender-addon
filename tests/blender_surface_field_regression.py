"""Field-aligned adaptive density, topology guards, guides and accept blocking."""

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


def wait():
    deadline = time.monotonic()+90
    while runtime.solve_stage or runtime.busy:
        assert time.monotonic() < deadline, runtime.last_error
        delay = runtime.poll()
        time.sleep(delay if delay is not None else .002)
    assert not runtime.last_error, runtime.last_error


def bumpy_sphere(name):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1)
    obj = bpy.context.object
    obj.name = name
    for vertex in obj.data.vertices:
        direction = vertex.co.normalized()
        bump = .10*max(0.0, np.sin(7*direction.x)*np.sin(7*direction.y)*np.sin(7*direction.z))
        vertex.co = direction*(1+bump)
    obj.data.update()
    return obj


remi.register()
try:
    settings = bpy.context.scene.remi_instant_meshes
    settings.target_faces = 1500
    settings.deterministic = True
    settings.preserve_creases = False
    settings.auto_guides = False
    settings.adaptive_method = "SURFACE"
    settings.adaptive_error = .01
    settings.adaptive_face_limit = 25000
    settings.adaptive_max_seconds = 60
    source = bumpy_sphere("Field-aligned source")
    vertices = np.array([v.co[:] for v in source.data.vertices], np.float32)
    faces = np.array([p.vertices[:] for p in source.data.polygons], np.int32)
    count = len(bpy.context.scene.objects)
    runtime.start(source, settings)
    wait()
    settings.adaptive_quads = True
    runtime.update_preview()
    assert bpy.ops.remi.instant_meshes_accept.poll() is False
    wait()
    report = runtime.adaptive_report
    print("REMI_FIELD_REPORT", report, flush=True)
    assert report["backend"] == "FIELD_ALIGNED" and report["rebuilt"], report
    quality = report["output_quality"]
    for key, value in (("quad_fraction", 1), ("components", 1), ("boundary_edges", 0),
                       ("nonmanifold_edges", 0), ("euler_characteristic", 2)):
        assert quality[key] == value, (key, quality[key])
    assert report["ideal_valence_fraction"] >= .90, report["ideal_valence_fraction"]
    assert report["poles"] <= max(4, report["output_faces"]*.05), report["poles"]
    assert quality["aspect_p95"] <= 2.5 and quality["angle_error_p95_deg"] <= 35, quality
    assert report["sampled_patch_error_max"] <= report["shape_error_limit"], report
    assert report["density_search"] == "ERROR_DRIVEN"
    np.testing.assert_array_equal(np.array([v.co[:] for v in source.data.vertices], np.float32), vertices)
    np.testing.assert_array_equal(np.array([p.vertices[:] for p in source.data.polygons], np.int32), faces)
    assert len(bpy.context.scene.objects) == count and runtime.can_accept
    print("REMI_FIELD_ALIGNED_DENSITY", report["output_faces"], report["poles"],
          report["ideal_valence_fraction"], report["sampled_patch_error_max"], flush=True)
    result_faces = len(runtime.preview[1])
    assert bpy.ops.remi.instant_meshes_accept() == {"FINISHED"}
    accepted = bpy.context.active_object
    assert accepted != source and len(accepted.data.polygons) == result_faces
    assert all(len(p.vertices) == 4 for p in accepted.data.polygons)

    # Guides are carried into the field solve instead of silently ignored.
    runtime.shutdown()
    settings.adaptive_quads = False
    runtime.start(source, settings)
    wait()
    from remi.features.retopology.instant_meshes.guides import FieldStroke
    runtime.strokes = [FieldStroke(0, np.array([[0, 0, 1.05], [0, 0, -1.05]], np.float32),
                                  np.array([[0, 0, 1], [0, 0, -1]], np.float32),
                                  np.array([0, 1], np.int32))]
    settings.adaptive_quads = True
    settings.adaptive_error = .01
    runtime.update_preview()
    wait()
    assert runtime.adaptive_report["rebuilt"], runtime.adaptive_report
    assert runtime.adaptive_report["backend"] == "FIELD_ALIGNED"
    runtime.strokes = []
    runtime.shutdown()

    # A failed density search can never be accepted as adaptive output.
    settings.adaptive_quads = False
    settings.adaptive_error = .0001
    settings.adaptive_face_limit = 1000
    runtime.start(source, settings)
    wait()
    settings.adaptive_quads = True
    runtime.update_preview()
    wait()
    assert runtime.adaptive_failed and not runtime.can_accept, runtime.adaptive_report
    assert "NOT APPLIED" in settings.status, settings.status
    assert not bpy.ops.remi.instant_meshes_accept.poll()
    assert not bpy.ops.remi.instant_meshes_session_accept.poll()
    assert bpy.ops.remi.instant_meshes_use_native() == {"FINISHED"}
    assert runtime.can_accept and not settings.adaptive_quads
    np.testing.assert_array_equal(np.array([v.co[:] for v in source.data.vertices], np.float32), vertices)
finally:
    runtime.shutdown()
    remi.unregister()

print("REMI_FIELD_ALIGNED_REGRESSION_OK", flush=True)
