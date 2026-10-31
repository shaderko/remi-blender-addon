"""Real event-loop cancellation while the owned surface optimizer is running."""
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
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.output.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
addon = importlib.util.module_from_spec(spec)
sys.modules["remi"] = addon
spec.loader.exec_module(addon)
addon.register()
from remi.blender.gaussian_splats import import_splats
from remi.blender.reconstructed_mesh import mesh_for_splat
from remi.features.generation.mesh_operators import runtime

splat = import_splats(bpy.context, args.archive, display="NATIVE")
settings = bpy.context.scene.remi_generation
settings.mesh_quality = "DENSE"
settings.mesh_output = str(args.output.resolve())
state = bpy.context.window_manager.remi_generation
started = time.monotonic()
job = None
temporary = None
requested = False


def tick():
    global job, temporary, requested
    try:
        if time.monotonic() - started > 120:
            raise TimeoutError("mesh cancellation smoke timed out")
        if job is None:
            assert bpy.ops.remi.create_mesh("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
            job, temporary = runtime.job, Path(runtime.temp_archive)
            assert state.mesh_busy and temporary.is_file()
        if not requested and job.status().get("phase", "").startswith("Optimizing surface"):
            assert job.poll() is None
            assert bpy.ops.remi.cancel_mesh() == {"FINISHED"}
            requested = True
        if requested and not state.mesh_busy:
            assert runtime.job is None and runtime.timer is None
            assert job.process.poll() is not None and not temporary.exists()
            assert state.mesh_status_kind == "CANCELLED", state.mesh_status
            assert mesh_for_splat(splat) is None and not splat.hide_get()
            assert not (job.directory / "complete.json").exists()
            assert not (job.directory / "mesh.ply").exists()
            (args.output / "cancel_modal_passed.txt").write_text("Cancelled owned optimizer; source preserved\n")
            print("REMI_MESH_CANCEL_MODAL_OK", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        if not requested and job.poll() is not None:
            raise AssertionError(job.status())
        return 0.2
    except Exception:
        traceback.print_exc()
        runtime.stop()
        import os
        os._exit(1)


bpy.app.timers.register(tick, first_interval=1)
