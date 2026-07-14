"""Topology regressions for the embedded Instant Meshes extraction."""

import sys
import time
from pathlib import Path

import bpy


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
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


remi.register()
from remi.instant_meshes.runtime import runtime


run_case(runtime, "BeveledCube", create_beveled_cube, 1000, True, True)
run_case(runtime, "DenseSphere", create_dense_sphere, 3000, False, True)
run_case(runtime, "OpenSuzanne", create_open_suzanne, 2000, False, False)

clear_scene(runtime)
remi.unregister()
print("REMI_INSTANT_MESHES_TOPOLOGY_OK")
