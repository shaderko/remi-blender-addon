"""Optional real Blender event-loop test for Create Mesh.

Run WITHOUT --background:
  blender --factory-startup --python tests/blender_mesh_modal_smoke.py -- \
      --archive path/to/splats.npz [--python path/to/python.exe]
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
parser.add_argument("--output", type=Path, default=ROOT / "generations" / "mesh_modal_smoke")
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
from remi.features.generation.mesh_operators import runtime as mesh_runtime

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
splat = import_splats(bpy.context, args.archive, display="NATIVE")
settings = bpy.context.scene.remi_generation
settings.python = args.python or settings.python
settings.mesh_output = str((args.output / "meshes").resolve())
started = time.monotonic()
state = bpy.context.window_manager.remi_generation


def finish():
    if time.monotonic() - started > 180:
        raise TimeoutError("mesh modal smoke timed out")
    if state.mesh_busy:
        return 0.5
    active = bpy.context.view_layer.objects.active
    assert active and active.get("remi_reconstructed_mesh"), state.mesh_status
    assert active.type == "MESH" and len(active.data.polygons) > 10000
    assert len(active.data.color_attributes) >= 1
    assert len(active.data.materials) == 1
    assert bpy.context.scene.remi_flow.preset == "RECONSTRUCTED"
    assert splat.hide_get()
    assert bpy.ops.remi.start_session.poll()
    (args.output / "mesh_modal_smoke_passed.txt").write_text(
        f"Create Mesh modal passed: {len(active.data.polygons):,} triangles\n"
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "mesh_modal_smoke.blend"))
    print(f"MESH MODAL SMOKE PASSED: {args.output}", flush=True)
    bpy.ops.wm.quit_blender()
    return None


def begin():
    try:
        assert bpy.ops.remi.create_mesh("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
        assert state.mesh_busy
        bpy.app.timers.register(finish, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        mesh_runtime.stop()
        import os
        os._exit(1)
    return None


bpy.app.timers.register(begin, first_interval=1.0)
