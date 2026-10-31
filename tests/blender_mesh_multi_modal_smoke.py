"""Real Blender event-loop test: two independent splats in one scene."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--output", type=Path, default=ROOT / "generations" / "mesh_multi_modal_smoke")
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
from remi.blender.reconstructed_mesh import mesh_for_splat
from remi.features.generation.mesh_operators import runtime as mesh_runtime
from remi.integrations.triposplat.splat_data import load, save

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
root = Path(tempfile.mkdtemp(prefix="remi-multi-", dir=args.output))
arrays1 = load(args.archive)
arrays2 = {name: value.copy() for name, value in arrays1.items()}
arrays2["positions"][:, 0] += 2.0
archive1, archive2 = root / "one.npz", root / "two.npz"
save(archive1, arrays1)
save(archive2, arrays2)
splat1 = import_splats(bpy.context, archive1, display="NATIVE")
splat2 = import_splats(bpy.context, archive2, display="NATIVE")
archive1.unlink()
archive2.unlink()
settings = bpy.context.scene.remi_generation
settings.mesh_quality = "FAST"
settings.mesh_output = str((args.output / "meshes").resolve())
state = bpy.context.window_manager.remi_generation
started = time.monotonic()
phase = 0


def _finish():
    global phase
    if time.monotonic() - started > 300:
        raise TimeoutError("multi-model mesh smoke timed out")
    if state.mesh_busy:
        return 0.5
    if phase == 0:
        mesh1 = mesh_for_splat(splat1)
        assert mesh1 is not None, state.mesh_status
        assert mesh1 is bpy.context.view_layer.objects.active
        assert mesh_for_splat(splat2) is None
        assert splat1.hide_get() and not splat2.hide_get()
        bpy.context.view_layer.objects.active = splat2
        splat2.select_set(True)
        assert bpy.ops.remi.create_mesh.poll()
        assert bpy.ops.remi.create_mesh("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
        phase = 1
        return 0.5
    mesh1, mesh2 = mesh_for_splat(splat1), mesh_for_splat(splat2)
    assert mesh1 is not None and mesh2 is not None, state.mesh_status
    assert mesh1 is not mesh2
    assert splat1["remi_mesh_object"] == mesh1.name
    assert splat2["remi_mesh_object"] == mesh2.name
    assert mesh1["remi_mesh_source_object"] == splat1.name
    assert mesh2["remi_mesh_source_object"] == splat2.name
    assert splat1.hide_get() and splat2.hide_get()
    assert len(mesh1.data.polygons) > 10000 and len(mesh2.data.polygons) > 10000
    (args.output / "mesh_multi_modal_smoke_passed.txt").write_text(
        f"Two independent meshes: {len(mesh1.data.polygons):,}, {len(mesh2.data.polygons):,}\n"
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "mesh_multi_modal_smoke.blend"))
    print(f"MESH MULTI-MODEL SMOKE PASSED: {args.output}", flush=True)
    bpy.ops.wm.quit_blender()
    return None


def finish():
    try:
        return _finish()
    except Exception:
        traceback.print_exc()
        mesh_runtime.stop()
        import os
        os._exit(1)


def begin():
    try:
        bpy.context.view_layer.objects.active = splat1
        splat1.select_set(True)
        splat2.select_set(False)
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
