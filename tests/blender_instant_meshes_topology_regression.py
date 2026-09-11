"""Topology regressions for the embedded Instant Meshes extraction."""

import sys
import time
from pathlib import Path

import bpy


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import remi


def wait_for_pipeline(runtime, timeout=60.0):
    started = time.time()
    while runtime.session is not None and (runtime.session.active or runtime.solve_stage):
        if time.time() - started > timeout:
            raise RuntimeError("Native Instant Meshes solver timed out")
        runtime.poll()
        time.sleep(0.02)


def clear_scene(runtime):
    runtime.shutdown()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def run_case(runtime, name, create, target_faces, preserve_creases, closed):
    clear_scene(runtime)
    create()
    source = bpy.context.active_object
    source.name = name

    settings = bpy.context.scene.remi_instant_meshes
    settings.target_faces = target_faces
    settings.pure_quad = True
    settings.preserve_creases = preserve_creases
    settings.align_boundaries = True
    settings.auto_update_preview = True
    runtime.start(source, settings)
    wait_for_pipeline(runtime)

    assert runtime.preview is not None, runtime.last_error
    assert runtime.preview_topology["nonmanifold_edges"] == 0
    assert runtime.preview_topology["components"] <= max(
        8, runtime.source_topology["components"] * 4
    )
    if closed:
        assert runtime.source_topology["boundary_edges"] == 0
        assert runtime.preview_topology["boundary_edges"] == 0
        assert runtime.preview_topology["components"] == runtime.source_topology["components"]
    print(name, runtime.source_topology, runtime.preview_topology)
    return source


def create_beveled_cube():
    bpy.ops.mesh.primitive_cube_add()
    modifier = bpy.context.active_object.modifiers.new("Rounded sharp case", "BEVEL")
    modifier.width = 0.15
    modifier.segments = 3


def create_dense_sphere():
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1.0)


def create_open_suzanne():
    bpy.ops.mesh.primitive_monkey_add()
    modifier = bpy.context.active_object.modifiers.new("Open surface case", "SUBSURF")
    modifier.levels = 2
    modifier.render_levels = 2


def wait_for_session(session, timeout=60.0):
    started = time.time()
    while session.active:
        if time.time() - started > timeout:
            raise RuntimeError("Native Instant Meshes solver timed out")
        time.sleep(0.02)


def find_defect_case(runtime, seed_target=1500, candidates=(4000, 5000, 6000, 9000)):
    """Re-seed a live session until extraction reports a quality defect.

    Re-seeding bypasses the automatic rebuild that normally keeps the density
    within the input mesh's resolution, which is exactly the regime where
    extraction used to refuse the result outright.
    """
    clear_scene(runtime)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1.0)
    source = bpy.context.active_object
    source.name = "DefectProbe"

    settings = bpy.context.scene.remi_instant_meshes
    settings.target_faces = seed_target
    settings.pure_quad = True
    settings.align_boundaries = True
    settings.auto_update_preview = True
    runtime.start(source, settings)
    wait_for_pipeline(runtime)

    for target in candidates:
        runtime.session.set_target_faces(target)
        runtime.solve_orientation()
        wait_for_pipeline(runtime)
        if runtime.preview_warnings:
            return target, list(runtime.preview_warnings)
    return None, []


def test_defects_are_reported_not_refused(runtime):
    """A defective extraction must warn and stay usable, not be refused."""
    target, warnings = find_defect_case(runtime)
    assert target is not None, "no defect configuration found to exercise the warning path"
    # The preview exists despite the defect: the user can look at it and accept.
    assert runtime.preview is not None, runtime.last_error
    assert runtime.preview_topology["boundary_edges"] > 0 or runtime.preview_topology[
        "degenerate_faces"
    ] > 0
    status = bpy.context.scene.remi_instant_meshes.status
    assert warnings[0] in status, status
    print("WARN", target, warnings)

    # Strict mode is still available so unattended runs can keep refusing.
    refused = False
    try:
        runtime.session.extract(strict=True)
    except Exception:
        refused = True
    assert refused, "strict extraction must still refuse a defective result"

    # Accepting must work with the warning present.
    bpy.ops.object.select_all(action="DESELECT")
    source = runtime.source
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    assert bpy.ops.remi.instant_meshes_accept() == {"FINISHED"}
    accepted = bpy.data.objects.get(source.name + "_instant")
    assert accepted is not None and len(accepted.data.polygons) > 0
    print("WARN_ACCEPTED", len(accepted.data.polygons))


def test_face_count_is_debounced(runtime):
    """Editing the face count re-solves once, after the edit settles."""
    clear_scene(runtime)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1.0)
    source = bpy.context.active_object
    source.name = "DebounceProbe"

    settings = bpy.context.scene.remi_instant_meshes
    settings.target_faces = 2000
    settings.pure_quad = True
    settings.align_boundaries = True
    settings.auto_update_preview = True
    runtime.start(source, settings)
    wait_for_pipeline(runtime)
    before = len(runtime.preview[1])

    # An edit queues work but must not re-solve while the user is still editing.
    settings.target_faces = 8000
    assert runtime.pending_target_faces == 8000
    assert runtime.target_faces_deadline > time.monotonic()
    assert len(runtime.preview[1]) == before
    first_deadline = runtime.target_faces_deadline

    # A second edit moves the deadline rather than queueing a second solve.
    settings.target_faces = 12000
    assert runtime.pending_target_faces == 12000
    assert runtime.target_faces_deadline > first_deadline
    assert len(runtime.preview[1]) == before

    # Once the value settles, the preview re-densifies to the requested count.
    runtime.target_faces_deadline = 0.0
    assert runtime.flush_target_faces() is True
    wait_for_pipeline(runtime)
    after = len(runtime.preview[1])
    assert after > before * 2, (before, after)
    assert abs(after - 12000) < 0.25 * 12000, after
    assert runtime.pending_target_faces is None
    print("DEBOUNCE", before, after)


remi.register()
from remi.features.retopology.instant_meshes.runtime import runtime


run_case(runtime, "BeveledCube", create_beveled_cube, 1000, True, True)
run_case(runtime, "DenseSphere", create_dense_sphere, 3000, False, True)
run_case(runtime, "OpenSuzanne", create_open_suzanne, 2000, False, False)

test_defects_are_reported_not_refused(runtime)
test_face_count_is_debounced(runtime)

clear_scene(runtime)
remi.unregister()
print("REMI_INSTANT_MESHES_TOPOLOGY_OK")
