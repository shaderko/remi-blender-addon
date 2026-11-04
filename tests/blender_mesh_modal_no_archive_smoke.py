"""Real Blender event-loop test: Create Mesh after deleting the source archive."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--python", default="")
parser.add_argument("--quality", choices=("DENSE", "FAST"), default="DENSE")
parser.add_argument("--output", type=Path, default=ROOT / "generations" / "mesh_no_archive_smoke")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.output.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str((args.output / "temporary").resolve())
(args.output / "temporary").mkdir(exist_ok=True)
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
from remi.blender.gaussian_splats import import_splats, splat_arrays
from remi.blender.reconstructed_mesh import mesh_for_splat
from remi.features.generation.mesh_operators import runtime as mesh_runtime

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
source_copy = Path(tempfile.mkdtemp(prefix="remi-no-archive-", dir=args.output)) / "splats.npz"
shutil.copy2(args.archive, source_copy)
splat = import_splats(bpy.context, source_copy, display="NATIVE")
source_copy.unlink()
assert not source_copy.exists()
assert len(splat_arrays(splat)["positions"]) > 0
settings = bpy.context.scene.remi_generation
settings.python = args.python or settings.python
settings.mesh_quality = args.quality
settings.mesh_output = str((args.output / "meshes").resolve())
splat.matrix_world = Matrix.Translation((1.2, -0.8, 0.4)) @ Matrix.Diagonal((1.1, 0.8, 1.3, 1.0))
state = bpy.context.window_manager.remi_generation
started = time.monotonic()


def _finish():
    if time.monotonic() - started > 600:
        raise TimeoutError("mesh modal smoke timed out")
    if state.mesh_busy:
        return 0.5
    active = bpy.context.view_layer.objects.active
    assert active and active.get("remi_reconstructed_mesh"), state.mesh_status
    assert active.type == "MESH" and len(active.data.polygons) > 10000
    assert len(active.data.color_attributes) >= 1
    assert len(active.data.materials) == 1
    assert mesh_for_splat(splat) is active
    assert active.matrix_world == splat.matrix_world
    assert active["remi_mesh_source_object"] == splat.name
    import json
    report = json.loads(Path(active["remi_mesh_report"]).read_text())
    assert report["quality"] == args.quality and report["schema"] == 2
    assert (Path(active["remi_mesh_ply"]).parent / "source.npz").is_file()
    assert splat.get("remi_mesh_object") == active.name
    assert bpy.context.scene.remi_flow.preset == "RECONSTRUCTED"
    (args.output / "mesh_no_archive_smoke_passed.txt").write_text(
        f"Create Mesh without source archive passed: {len(active.data.polygons):,} triangles\n"
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "mesh_no_archive_smoke.blend"))
    print(f"MESH NO-ARCHIVE SMOKE PASSED: {args.output}", flush=True)
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
        assert bpy.ops.remi.create_mesh("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
        assert state.mesh_busy
        splat.name = "Renamed while creating surface"
        bpy.app.timers.register(finish, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        mesh_runtime.stop()
        import os
        os._exit(1)
    return None


bpy.app.timers.register(begin, first_interval=1.0)