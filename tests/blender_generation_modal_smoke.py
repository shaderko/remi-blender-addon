"""Optional real CUDA + Blender event-loop smoke; run WITHOUT --background."""
import argparse
import importlib.util
from pathlib import Path
import sys
import time
import traceback

import bpy
from mathutils import Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--image", required=True)
parser.add_argument("--python", default="", help="Optional external Python override; defaults to automatic detection")
parser.add_argument("--models", default=str(ROOT / "models" / "triposplat"))
parser.add_argument("--output", default=str(ROOT / "generations"))
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
temporary = Path(args.output) / ".blender-smoke"
temporary.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temporary.resolve())
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
started = time.monotonic()
completed_directory = None
cancellation_process = None


def finish():
    global completed_directory, cancellation_process
    state = bpy.context.window_manager.remi_generation
    if time.monotonic() - started > 240:
        raise TimeoutError("Generation modal smoke timed out")
    if state.busy:
        return 0.5
    if cancellation_process is not None:
        from remi.features.generation.operators import runtime
        assert runtime.job is None
        assert cancellation_process.poll() is not None
        assert state.status.startswith("Cancelled"), state.status
        assert state.status_kind == "CANCELLED"
        assert sum(bool(obj.get("remi_gaussian_splats")) for obj in bpy.context.scene.objects) == 1
        (completed_directory / "modal_smoke_passed.txt").write_text(
            "Actual modal generation, lossless native import, PLY parity, preview render and cancellation passed.\n"
        )
        print(f"MODAL GENERATION SMOKE PASSED: {completed_directory}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    obj = bpy.context.active_object
    assert obj and obj.get("remi_gaussian_splats"), state.status
    assert state.status_kind == "SUCCESS"
    assert obj["remi_splat_count"] == 65536
    from remi.blender.gaussian_splats import native_splats_available, uses_native_splats
    native_display = uses_native_splats(obj)
    assert native_display == native_splats_available()
    if native_display:
        assert len(obj.data.points) == 65536 and not obj.modifiers
    assert not bpy.ops.remi.start_session.poll()
    assert "torch" not in sys.modules
    directory = Path(state.directory)
    assert (directory / "model.ply").is_file()
    # Both formats must retain identical native coordinates/SH/quaternions;
    # PLY stores log scales and logit opacity instead of activated values.
    from remi.integrations.triposplat.splat_data import load
    native = load(directory / "splats.npz")
    with (directory / "model.ply").open("rb") as stream:
        fields = []
        while (line := stream.readline().decode("ascii").strip()) != "end_header":
            if line.startswith("property float "):
                fields.append((line.split()[-1], "<f4"))
        ply = np.fromfile(stream, dtype=np.dtype(fields))
    for names, expected in ((("x", "y", "z"), native["positions"]),
                            (("f_dc_0", "f_dc_1", "f_dc_2"), native["sh_dc"]),
                            (("rot_0", "rot_1", "rot_2", "rot_3"), native["rotations"])):
        np.testing.assert_allclose(np.stack([ply[name] for name in names], axis=1), expected, atol=1e-6)
    np.testing.assert_allclose(np.exp(np.stack([ply[f"scale_{i}"] for i in range(3)], axis=1)), native["scales"], rtol=1e-5)
    np.testing.assert_allclose(1 / (1 + np.exp(-ply["opacity"])), native["opacities"], atol=1e-6)
    positions = native["positions"]
    low, high = np.quantile(positions, [0.01, 0.99], axis=0)
    center = Vector((low + high) / 2)
    extent = max(float(np.max(high - low)), 0.1)
    for other in list(bpy.context.scene.objects):
        if other is not obj:
            bpy.data.objects.remove(other, do_unlink=True)
    camera_data = bpy.data.cameras.new("Preview Camera")
    camera = bpy.data.objects.new("Preview Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = center + Vector((1.6, -3.0, 1.2)) * extent
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = extent * 1.6
    scene = bpy.context.scene
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 8
    scene.render.resolution_x = scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(directory / ("native_gaussians.png" if native_display else "ellipsoid_preview.png"))
    scene.world.color = (0.15, 0.15, 0.15)
    scene.view_settings.view_transform = "Standard"
    bpy.ops.wm.save_as_mainfile(filepath=str(directory / "splats_preview.blend"))
    bpy.ops.render.render(write_still=True)
    completed_directory = directory
    assert bpy.ops.remi.generate_splats("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
    from remi.features.generation.operators import runtime
    cancellation_process = runtime.job.process
    assert bpy.ops.remi.cancel_generation() == {"FINISHED"}
    return 0.5


def guarded_finish():
    try:
        return finish()
    except Exception:
        traceback.print_exc()
        from remi.features.generation.operators import runtime
        runtime.stop()
        # Explicit failure exit rather than leaving an unattended Blender open.
        import os
        os._exit(1)


def begin():
    try:
        settings = bpy.context.scene.remi_generation
        settings.image, settings.python, settings.models, settings.output = args.image, args.python, args.models, args.output
        result = bpy.ops.remi.generate_splats("INVOKE_DEFAULT")
        assert result == {"RUNNING_MODAL"}, result
        assert bpy.context.window_manager.remi_generation.status_kind == "PROGRESS"
        assert not bpy.ops.remi.select_workspace.poll()
        bpy.app.timers.register(guarded_finish, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        import os
        os._exit(1)
    return None


bpy.app.timers.register(begin, first_interval=1.0)
