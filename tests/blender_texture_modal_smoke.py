"""Optional real Blender event-loop test for Texture projection.

Run WITHOUT --background:
  blender --factory-startup --python tests/blender_texture_modal_smoke.py -- \
      --archive path/to/splats.npz --python path/to/python.exe
"""
import argparse
import importlib.util
from pathlib import Path
import sys
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--python", default="")
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
splat = import_splats(bpy.context, args.archive, display="AUTO")
bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=0.5)
mesh = bpy.context.active_object
mesh.name = "TextureSmokeSphere"
mesh["remi_splat_source"] = splat
bpy.context.view_layer.objects.active = mesh
mesh.select_set(True)
splat.select_set(False)

settings = bpy.context.scene.remi_generation
settings.python = args.python or settings.python
texture = bpy.context.scene.remi_texture
texture.texture_size = "1024"
texture.samples = 8
texture.output = str((args.output / "textures").resolve())
state = bpy.context.window_manager.remi_texture
started = time.monotonic()


def finish():
    if time.monotonic() - started > 240:
        raise TimeoutError("texture modal smoke timed out")
    if state.busy:
        return 0.5
    active = bpy.context.view_layer.objects.active
    assert active == mesh, state.status
    assert state.status_kind == "SUCCESS", state.status
    assert active.get("remi_texture_albedo"), state.status
    assert active.get("remi_texture_normal"), state.status
    assert active.get("remi_texture_confidence"), state.status
    assert active.data.materials and active.data.materials[0].use_nodes
    assert bpy.ops.remi.texture_preview.poll()
    (args.output / "texture_modal_smoke_passed.txt").write_text(
        f"Texture modal passed: {active.get('remi_texture_coverage', 0):.0%} coverage\n"
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "texture_modal_smoke.blend"))
    print(f"TEXTURE MODAL SMOKE PASSED: {args.output}", flush=True)
    bpy.ops.wm.quit_blender()
    return None


def begin():
    try:
        assert bpy.ops.remi.select_workspace(workspace="TEXTURE") == {"FINISHED"}
        assert bpy.ops.remi.project_splats("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
        assert state.busy
        bpy.app.timers.register(finish, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        texture_runtime.stop()
        import os
        os._exit(1)
    return None


bpy.app.timers.register(begin, first_interval=1.0)
