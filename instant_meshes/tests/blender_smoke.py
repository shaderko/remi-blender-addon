"""Run with: blender --factory-startup --background --python blender_smoke.py"""

import sys
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import remi


def wait_for_pipeline(runtime, timeout=20.0):
    started = time.time()
    while runtime.session is not None and (runtime.session.active or runtime.solve_stage):
        if time.time() - started > timeout:
            raise RuntimeError("Native Instant Meshes solver timed out")
        runtime.poll()
        time.sleep(0.02)


remi.register()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.0)
source = bpy.context.active_object
source.name = "InteractiveSource"

settings = bpy.context.scene.remi_instant_meshes
settings.target_faces = 500
settings.pure_quad = True
settings.preserve_creases = False
assert bpy.ops.remi.instant_meshes_start() == {"FINISHED"}

from remi.instant_meshes.runtime import runtime

wait_for_pipeline(runtime)
assert runtime.orientation is not None
assert runtime.position is not None
assert runtime.position_ready
assert runtime.preview is not None
assert runtime.preview_normals.shape == runtime.preview[0].shape
assert runtime.average_edge_length > 0
assert runtime.preview_topology["components"] == 1
assert runtime.preview_topology["boundary_edges"] == 0
assert runtime.preview_topology["nonmanifold_edges"] == 0

# Native extraction must never accept the random/stale position field after an
# orientation-only re-solve. This was the cause of shredded output meshes.
runtime.session.start_orientation()
while runtime.session.active:
    time.sleep(0.02)
try:
    runtime.session.extract()
except RuntimeError as error:
    assert "position field" in str(error).lower()
else:
    raise AssertionError("Orientation-only extraction was not rejected")

runtime.solve_orientation()
wait_for_pipeline(runtime)

hits = []
for face_index in (0, 1, 2, 3):
    face = runtime.surface_faces[face_index]
    point = runtime.surface_vertices[face].mean(axis=0)
    normal = runtime.surface_normals[face].mean(axis=0)
    normal /= (normal * normal).sum() ** 0.5
    hits.append((point, normal, face_index))

runtime.add_stroke(1, hits)
wait_for_pipeline(runtime)
assert runtime.position is not None
assert runtime.session.stroke_count == 1
assert runtime.preview is not None
assert runtime.preview_normals.shape == runtime.preview[0].shape
assert runtime.preview_topology["components"] == 1
assert runtime.preview_topology["boundary_edges"] == 0
assert runtime.preview_topology["nonmanifold_edges"] == 0

assert len(runtime.preview[0]) > 0
assert len(runtime.preview[1]) > 0
assert bpy.ops.remi.instant_meshes_accept() == {"FINISHED"}

output = bpy.context.active_object
assert output is not source
assert len(output.data.polygons) > 0
assert max(len(face.vertices) for face in output.data.polygons) == 4
assert bpy.data.objects.get("InteractiveSource") is source
from remi.uv_mapping import ensure_remi_uv

uv_result = ensure_remi_uv(
    output,
    profile_id="BALANCED",
    texture_size=512,
    margin_px=4,
    preserve_existing_seams=False,
    replace_existing=True,
)
assert uv_result.success, uv_result.error
assert uv_result.stats is not None and uv_result.stats.valid
print("REMI_INSTANT_MESHES_SMOKE_OK", len(output.data.vertices), len(output.data.polygons))
print(
    "REMI_INSTANT_MESHES_UV_OK",
    uv_result.solver,
    uv_result.stats.chart_count,
    uv_result.stats.flipped_triangles,
)
remi.unregister()
