"""Optional real Blender event-loop test for Texture projection.

Run WITHOUT --background:
  blender --factory-startup --python tests/blender_texture_modal_smoke.py -- \
      --archive path/to/splats.npz --python path/to/python.exe
"""
import argparse
import importlib.util
from pathlib import Path
import sys
import shutil
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--python", default="")
parser.add_argument("--method", choices=("FIELD", "OCTREE"), default="FIELD")
parser.add_argument("--low-poly", action="store_true")
parser.add_argument("--delete-archive", action="store_true")
parser.add_argument("--cancel", action="store_true", help="Cancel the octree job while tracing, before image import")
parser.add_argument("--output", type=Path, default=ROOT / "generations" / "texture_modal_smoke")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.output.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str((args.output / "temporary").resolve())
(args.output / "temporary").mkdir(exist_ok=True)
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
from remi.blender.gaussian_splats import import_splats
from remi.features.texture.operators import runtime as texture_runtime

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
source_archive = args.archive
if args.delete_archive:
    source_archive = args.output / "temporary_source.npz"
    shutil.copy2(args.archive, source_archive)
splat = import_splats(bpy.context, source_archive, display="AUTO")
if args.delete_archive:
    source_archive.unlink()
bpy.ops.mesh.primitive_uv_sphere_add(segments=16 if args.low_poly else 48,
                                    ring_count=12 if args.low_poly else 24, radius=0.5)
mesh = bpy.context.active_object
mesh.name = "TextureSmokeSphere"
original_vertices = len(mesh.data.vertices)
mesh["remi_splat_source"] = splat
bpy.context.view_layer.objects.active = mesh
mesh.select_set(True)
splat.select_set(False)

settings = bpy.context.scene.remi_generation
settings.python = args.python or settings.python
texture = bpy.context.scene.remi_texture
texture.texture_size = "1024"
texture.samples = 8
texture.bake_method = args.method
texture.output = str((args.output / "textures").resolve())
state = bpy.context.window_manager.remi_texture
started = time.monotonic()
cancel_requested = False
captured_job = None
captured_paths = ()


def _finish():
    global cancel_requested
    if time.monotonic() - started > 900:
        raise TimeoutError("texture modal smoke timed out")
    if state.busy:
        if args.cancel and not cancel_requested and texture_runtime.job.status().get("phase", "").startswith("Tracing"):
            assert bpy.ops.remi.cancel_projection() == {"FINISHED"}
            cancel_requested = True
        return 0.5
    if args.cancel:
        assert cancel_requested and state.status_kind == "CANCELLED", state.status
        assert texture_runtime.job is None and captured_job.process.poll() is not None
        assert all(not Path(path).exists() for path in captured_paths)
        assert not mesh.get("remi_texture_albedo") and len(mesh.data.vertices) == original_vertices
        (args.output / "texture_cancel_smoke_passed.txt").write_text("Cancelled ray tracing; no images imported\n")
        print(f"TEXTURE CANCEL SMOKE PASSED: {args.output}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    active = bpy.context.view_layer.objects.active
    assert active == mesh, state.status
    assert state.status_kind == "SUCCESS", state.status
    assert active.get("remi_texture_albedo"), state.status
    assert active.get("remi_texture_normal"), state.status
    assert active.get("remi_texture_confidence"), state.status
    assert active.data.materials and active.data.materials[0].use_nodes
    assert len(mesh.data.vertices) == original_vertices
    assert mesh["remi_texture_method"] == ("OCTREE_LOCAL" if args.method == "OCTREE" else args.method)
    assert len(mesh.remi_texture_layers) == 1 and mesh.remi_texture_layers[0].kind == "BASE"
    normal_image = bpy.data.images.get(mesh["remi_texture_normal_image"])
    assert normal_image.colorspace_settings.name == "Non-Color"
    assert any(node.type == "NORMAL_MAP" for node in mesh.data.materials[0].node_tree.nodes)
    assert bpy.ops.remi.texture_preview.poll()
    (args.output / "texture_modal_smoke_passed.txt").write_text(
        f"Texture modal passed: {active.get('remi_texture_coverage', 0):.0%} coverage\n"
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "texture_modal_smoke.blend"))
    print(f"TEXTURE MODAL SMOKE PASSED: {args.output}", flush=True)
    bpy.ops.wm.quit_blender()
    return None


def finish():
    try:
        return _finish()
    except Exception:
        traceback.print_exc()
        texture_runtime.stop()
        import os
        os._exit(1)


def begin():
    global captured_job, captured_paths
    try:
        assert bpy.ops.remi.select_workspace(workspace="TEXTURE") == {"FINISHED"}
        assert bpy.ops.remi.project_splats("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
        assert state.busy
        captured_job, captured_paths = texture_runtime.job, texture_runtime.temp_paths
        bpy.app.timers.register(finish, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        texture_runtime.stop()
        import os
        os._exit(1)
    return None


bpy.app.timers.register(begin, first_interval=1.0)
