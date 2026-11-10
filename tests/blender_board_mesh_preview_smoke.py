"""Real mesh-card orbit, materials, isolation, mixed LRU and saved thumbnails.

Blender 5.3 --enable-event-simulate --python-exit-code 1 --python this.py.
Self-contained PLY/GLB/Gaussian fixtures; no inference or downloads.
"""

from datetime import datetime
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest import mock

import bpy
import gpu
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_mesh_preview_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas
from remi.features.board.assets import Asset
from remi.features.board.splat_preview import NativeMeshPreview
from remi.integrations.triposplat.splat_data import save as save_splats


DATA = ("scenes", "screens", "objects", "meshes", "materials", "images", "node_groups", "textures",
        "collections", "armatures", "actions", "shape_keys", "cameras", "lights", "pointclouds")


def snapshot():
    return {name: set(getattr(bpy.data, name)) for name in DATA}


window, scene = bpy.context.window, bpy.context.scene
scene.remi_image.output = str(OUTPUT / "images")
scene.remi_generation.output = str(OUTPUT / "models")
original_objects = set(scene.objects)
original_names = set(scene.objects.keys())
original_active = bpy.context.view_layer.objects.active
original_selection = set(bpy.context.selected_objects)
before = snapshot()
GLB = OUTPUT / "multipart.glb"
material = bpy.data.materials.new("Textured preview fixture")
shader = material.node_tree.nodes.get("Principled BSDF")
for name, color in (("albedo", (0.08, 0.65, 0.9, 1)), ("normal", (0.5, 0.5, 1, 1))):
    image = bpy.data.images.new(name, 8, 8)
    image.generated_color = color
    image.filepath_raw = str(OUTPUT / (name + ".png"))
    image.file_format = "PNG"
    image.save()
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    if name == "albedo":
        material.node_tree.links.new(node.outputs["Color"], shader.inputs["Base Color"])
    else:
        image.colorspace_settings.name = "Non-Color"
        normal = material.node_tree.nodes.new("ShaderNodeNormalMap")
        material.node_tree.links.new(node.outputs["Color"], normal.inputs["Color"])
        material.node_tree.links.new(normal.outputs["Normal"], shader.inputs["Normal"])
bpy.ops.object.select_all(action="DESELECT")
bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.7, 0, 0))
first = bpy.context.object
first.scale = (1.1, 0.6, 1.5)
first.data.materials.append(material)
bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=0.6, location=(0.75, 0.25, 0.4))
bpy.context.object.data.materials.append(material)
first.select_set(True)
assert bpy.ops.export_scene.gltf(filepath=str(GLB), export_format="GLB", use_selection=True,
                                 use_active_scene=True) == {"FINISHED"}
bpy.data.batch_remove(ids=[item for name in DATA for item in set(getattr(bpy.data, name))-before[name]])
for obj in original_selection:
    obj.select_set(True)
bpy.context.view_layer.objects.active = original_active

PLY = OUTPUT / "uncolored.ply"
PLY.write_text("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
               "element face 4\nproperty list uchar int vertex_indices\nend_header\n"
               "-1 -1 0\n1 -1 0\n0 1 0\n0 0 1.5\n3 0 2 1\n3 0 1 3\n3 1 2 3\n3 2 0 3\n")
COLORED = OUTPUT / "colored.ply"
header, body = PLY.read_text().split("end_header\n")
header = header.replace("element face 4", "property uchar red\nproperty uchar green\nproperty uchar blue\nelement face 4")
rows = body.splitlines()
COLORED.write_text(header+"end_header\n"+"\n".join([row+" 220 60 15" for row in rows[:4]]+rows[4:])+"\n")
SPLAT = OUTPUT / "splats.npz"
save_splats(SPLAT, {"positions": np.array([[0, 0, 0], [0.4, 0, 0]], np.float32),
    "scales": np.full((2, 3), 0.15, np.float32),
    "rotations": np.tile(np.array([1, 0, 0, 0], np.float32), (2, 1)),
    "opacities": np.ones(2, np.float32), "sh_dc": np.zeros((2, 3), np.float32)})
hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (GLB, PLY, COLORED, SPLAT)}
step = waits = 0
renderer = freeze = None
renderer_ids = None
start_point = None
position = None
background_preview = None


def pixels(preview):
    with preview.offscreen.bind():
        buffer = gpu.state.active_framebuffer_get().read_color(0, 0, preview.size, preview.size, 4, 0, "FLOAT")
        return np.array(buffer, dtype=np.float32).reshape(preview.size, preview.size, 4)


def assert_source():
    assert window.scene == scene and set(scene.objects) == original_objects
    assert bpy.context.view_layer.objects.active == original_active
    assert set(bpy.context.selected_objects) == original_selection
    for path, digest in hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def mouse(kind, value, x, y, **modifiers):
    window.event_simulate(type=kind, value=value, x=x, y=y, **modifiers)


def advance():
    global step, waits, renderer, renderer_ids, freeze, start_point, position, window, scene, background_preview
    board = canvas.BOARD
    waits += 1
    assert waits < 100, f"Timed out at step {step}"
    if step == 0:
        baseline = snapshot()
        shading = [(area.spaces.active, area.spaces.active.shading.type) for area in window.screen.areas if area.type == "VIEW_3D"]
        for path, count in ((GLB, 2), (PLY, 1), (COLORED, 1)):
            preview = NativeMeshPreview(path, size=256, elevation=20)
            try:
                assert len([obj for obj in preview.scene.objects if obj.type == "MESH"]) == count
                front = pixels(preview)
                assert np.isfinite(front).all() and np.count_nonzero(front[:, :, 3] > 0.1) > 500
                assert front[:, :, :3].max() > 0.1, "Uncolored PLY rendered black"
                if path == GLB:
                    nodes = [node for obj in preview.scene.objects if obj.type == "MESH"
                             for mat in obj.data.materials for node in mat.node_tree.nodes]
                    assert any(node.type == "TEX_IMAGE" and node.image.size[0] == 8 for node in nodes)
                    assert any(node.type == "NORMAL_MAP" for node in nodes)
                elif path == COLORED:
                    assert preview.obj.data.color_attributes
                    visible = front[:, :, 3] > 0.9
                    assert front[visible, 0].mean() > front[visible, 2].mean()*2, "PLY vertex colors were lost"
                preview.save(OUTPUT / (path.stem + "-front.png"))
                ids = (preview.obj.as_pointer(), preview.screen.as_pointer(), preview.camera.as_pointer())
                preview.render(yaw=65, elevation=30)
                assert np.mean(np.abs(pixels(preview)-front)) > 0.005, "Orbit did not change the native mesh view"
                assert (preview.obj.as_pointer(), preview.screen.as_pointer(), preview.camera.as_pointer()) == ids
            finally:
                preview.free()
            assert snapshot() == baseline, "Native mesh preview leaked data"
        try:
            NativeMeshPreview(OUTPUT / "missing.glb")
        except ValueError:
            pass
        else:
            raise AssertionError("A missing mesh opened a pretend viewport")
        assert snapshot() == baseline, "Failed mesh preview leaked data"
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            preview = NativeMeshPreview(GLB, size=256)
            preview.free()
            assert bpy.context.mode == "EDIT_MESH", "Preview exited the user's Edit Mode"
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
        assert snapshot() == baseline, "Preview while editing leaked data"
        assert all(space.shading.type == value for space, value in shading)
        assert_source()
        bpy.ops.remi_board.open()
    elif step == 1:
        if board is None:
            return 0.2
        for index, (key, path) in enumerate((("mesh", GLB), ("ply", PLY))):
            board.catalog.assets[key] = Asset(key, "MESH", artifact=str(path), title=key,
                                             preview=str(OUTPUT / (path.stem+"-front.png")))
            board.positions[key] = [index*350.0, 70.0]
            board.scales[key] = 0.8
            board.order.append(key)
        board.zoom, board.pan = 0.8, [100.0, 300.0]
        canvas.save_layout(bpy.context)
    elif step == 2:
        if any(board.texture(key) is None for key in ("mesh", "ply")):
            return 0.2
        assert not board.loaded_previews, "Opening mesh cards eagerly loaded live models"
        background_preview = mock.Mock()
        background_preview.poll.return_value = None
        board.runner.preview_job, board.runner.preview_id = background_preview, "mesh"
        position = list(board.positions["mesh"])
        region = canvas.window_region(canvas.board_area(board))
        x, y, w, h = board.card_rect("mesh")
        start_point = int(region.x+x+w/2), int(region.y+y+h/2)
        x, y = start_point
        mouse("MOUSEMOVE", "NOTHING", x, y)
        mouse("LEFTMOUSE", "PRESS", x, y, alt=True)
        mouse("MOUSEMOVE", "NOTHING", x+70, y+22, alt=True)
    elif step == 3:
        asset = board.catalog.assets["mesh"]
        assert board.orbit_drag is not None and board.selected == {"mesh"}
        assert board.selected == set(canvas.selected_paths(bpy.context))
        assert 320 < asset.yaw < 340 and asset.elevation > 25
        if "mesh" not in board.loaded_previews:
            assert not board.preview_errors, board.preview_errors
            return 0.2
        renderer = board.loaded_previews["mesh"]["preview"]
        background_preview.cancel.assert_called_once()
        assert board.runner.preview_job is None, "An old background thumbnail could overwrite the orbit capture"
        renderer_ids = (renderer.obj.as_pointer(), renderer.screen.as_pointer())
        assert isinstance(renderer, NativeMeshPreview)
        assert board.texture("mesh")[0] == renderer.texture
        assert board.positions["mesh"] == position
        assert_source()
        freeze = mock.patch.object(canvas, "NativeMeshPreview", side_effect=AssertionError("Orbit re-imported the mesh"))
        freeze.start()
        x, y = start_point
        mouse("MOUSEMOVE", "NOTHING", x+100, y+37, alt=True)
    elif step == 4:
        assert (renderer.obj.as_pointer(), renderer.screen.as_pointer()) == renderer_ids
        assert board.catalog.assets["mesh"].yaw < 320
        freeze.stop()
        freeze = None
        x, y = start_point
        mouse("LEFTMOUSE", "RELEASE", x+100, y+37, alt=True)
    elif step == 5:
        if board.live_asset_id:
            return 0.2
        asset = board.catalog.assets["mesh"]
        assert board.orbit_drag is None and board.positions["mesh"] == position
        assert board.thumbnail_ready(asset)
        assert asset.preview != str(OUTPUT / "multipart-front.png")
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "mesh-orbit.png"))
        used = board.loaded_previews["mesh"]["last_used"]
        board.prepare_preview()
        assert board.loaded_previews["mesh"]["last_used"] == used, "Idle drawing extended the preview timer"
        board.expire_previews(now=used+29.9)
        assert "mesh" in board.loaded_previews
        board.expire_previews(now=used+30.1)
        assert not board.loaded_previews
        with mock.patch.object(canvas, "NativeMeshPreview", side_effect=AssertionError("Idle card loaded 3D")):
            board.load_texture("mesh")
            assert board.texture("mesh") is not None
    elif step == 6:
        identifiers = []
        for index, (kind, path) in enumerate((("MESH", GLB), ("SPLAT", SPLAT), ("MESH", PLY), ("MESH", GLB))):
            key = f"mixed:{index}"
            board.catalog.assets[key] = Asset(key, kind, artifact=str(path), yaw=index*10)
            identifiers.append(key)
            board.update_live_preview(key)
            board.finish_live_preview()
            assert len(board.loaded_previews) <= 3
        assert len(board.loaded_previews) == 3 and identifiers[0] not in board.loaded_previews
        warm = board.loaded_previews[identifiers[2]]["preview"]
        with mock.patch.object(canvas, "NativeMeshPreview", side_effect=AssertionError("Warm mesh re-imported")):
            board.catalog.assets[identifiers[2]].yaw = 48
            board.update_live_preview(identifiers[2])
            assert board.loaded_previews[identifiers[2]]["preview"] is warm
            board.finish_live_preview()
        assert_source()
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "mesh-preview.blend"))
        assert not board.loaded_previews
        assert not any(screen.get("remi_preview_screen") for screen in bpy.data.screens)
        assert not any("isolated native preview" in item.name for item in bpy.data.scenes)
        bpy.ops.wm.open_mainfile(filepath=str(OUTPUT / "mesh-preview.blend"))
    elif step == 7:
        window, scene = bpy.context.window, bpy.context.scene
        if board is None:
            return 0.2
        asset = board.catalog.assets["mesh"]
        assert asset.yaw < 320 and asset.elevation > 25 and board.thumbnail_ready(asset)
        assert not board.loaded_previews
        assert set(scene.objects.keys()) == original_names
        remi.unregister()
        print(f"REMI_BOARD_MESH_PREVIEW_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return 0.4


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        print(f"REMI_BOARD_MESH_PREVIEW_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=0.6, persistent=True)
