"""Optional real Blender event-loop test for camera-view splat projection.

Run WITHOUT --background:
  blender --factory-startup --python tests/blender_texture_view_modal_smoke.py -- \
      --archive path/to/splats.npz --python path/to/python.exe
"""
import argparse
import importlib.util
import math
from pathlib import Path
import sys
import time
import traceback

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--python", default="")
parser.add_argument("--orbit", action="store_true", help="Use the orbit viewport instead of camera view")
parser.add_argument("--output", type=Path, default=ROOT / "generations" / "texture_view_modal_smoke")
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
from remi.features.texture.projection_area import state as projection_area

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
splat = import_splats(bpy.context, args.archive, display="NATIVE")
bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=0.5)
mesh = bpy.context.active_object
mesh.name = "TextureViewSmokeSphere"
mesh["remi_splat_source"] = splat
for polygon in mesh.data.polygons:
    polygon.use_smooth = True
bpy.context.view_layer.objects.active = mesh
mesh.select_set(True)
splat.select_set(False)

bpy.ops.object.camera_add(location=(0, -2.0, 0), rotation=(math.pi / 2, 0, 0))
camera = bpy.context.active_object
camera.data.lens = 50
bpy.context.scene.camera = camera
camera.select_set(False)
bpy.context.view_layer.objects.active = mesh
mesh.select_set(True)
view_area = None
view_region = None
for area in bpy.context.screen.areas:
    if area.type == "VIEW_3D":
        area.spaces.active.region_3d.view_perspective = "PERSP" if args.orbit else "CAMERA"
        view_area = area
        view_region = next(region for region in area.regions if region.type == "WINDOW")
        break

assert hasattr(bpy.ops.remi, "draw_projection_area")
assert hasattr(bpy.ops.remi, "clear_projection_area")

settings = bpy.context.scene.remi_generation
settings.python = args.python or settings.python
texture = bpy.context.scene.remi_texture
texture.texture_size = "1024"
texture.view_resolution = "1024"
texture.pack_images = True
texture.output = str((args.output / "textures").resolve())
state = bpy.context.window_manager.remi_texture
started = time.monotonic()


def finish():
    if time.monotonic() - started > 240:
        raise TimeoutError("texture view modal smoke timed out")
    if state.busy:
        return 0.5
    active = bpy.context.view_layer.objects.active
    assert active == mesh, state.status
    assert state.status_kind == "SUCCESS", state.status
    assert len(mesh.remi_texture_layers) >= 1, state.status
    layer = mesh.remi_texture_layers[-1]
    assert layer.kind == "VIEW"
    assert Path(layer.image_path).is_file()
    assert mesh.data.materials and mesh.data.materials[0].name.endswith("layered")
    assert any(node.type == "MIX_RGB" for node in mesh.data.materials[0].node_tree.nodes)
    image = bpy.data.images.get(layer.image_name)
    assert image is not None
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    masked_texels = int(np.count_nonzero(pixels.reshape(-1, 4)[:, 3] > 0.01))
    assert 1000 < masked_texels < 160000, masked_texels
    assert not projection_area.has_points()
    (args.output / "texture_view_modal_smoke_passed.txt").write_text(
        f"View projection passed: {len(mesh.remi_texture_layers)} layer(s)\n"
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "texture_view_modal_smoke.blend"))
    print(f"TEXTURE VIEW MODAL SMOKE PASSED: {args.output}", flush=True)
    bpy.ops.wm.quit_blender()
    return None


def begin():
    try:
        assert bpy.ops.remi.select_workspace(workspace="TEXTURE") == {"FINISHED"}
        area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
        region = next(region for region in area.regions if region.type == "WINDOW")
        with bpy.context.temp_override(area=area, region=region):
            projection_area.begin(bpy.context, area, region, mesh)
            projection_area.points = [
                (0.0, 0.0),
                (projection_area.width * 0.5, 0.0),
                (projection_area.width * 0.5, projection_area.height),
                (0.0, projection_area.height),
            ]
            projection_area.drawing = False
            assert projection_area.valid(bpy.context)
            assert bpy.ops.remi.project_view.poll(), "Project from View poll failed"
            assert bpy.ops.remi.project_view("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
        assert state.busy
        bpy.app.timers.register(finish, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        texture_runtime.stop()
        import os
        os._exit(1)
    return None


bpy.app.timers.register(begin, first_interval=1.0)
