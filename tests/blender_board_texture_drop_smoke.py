"""Real card-body drop, fancy feedback, and a real Gaussian texture bake.

Blender 5.3 --enable-event-simulate --python-exit-code 1 --python this.py.
Uses synthetic sphere fixtures, not inference or downloaded models.
"""

from datetime import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import traceback
from unittest import mock

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_texture_drop_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, texture_effects
from remi.features.board.assets import Asset
from remi.integrations.triposplat.splat_data import save as save_splats


window = bpy.context.window
scene = bpy.context.scene
scene.remi_image.output = str(OUTPUT / "images")
scene.remi_generation.output = str(OUTPUT / "models")
scene.remi_texture.texture_size = "1024"
scene.remi_texture.match_distance = 0.05
scene.remi_texture.layer_width = 0.02
original_objects = set(scene.objects.keys())
bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, radius=0.95)
fixture = bpy.context.active_object
fixture.name = "Mesh fixture"
material = bpy.data.materials.new("Neutral mesh")
material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (0.22, 0.27, 0.32, 1)
fixture.data.materials.append(material)
for face in fixture.data.polygons:
    face.use_smooth = True
MESH = OUTPUT / "sphere.glb"
bpy.ops.object.select_all(action="DESELECT")
fixture.select_set(True)
assert bpy.ops.export_scene.gltf(filepath=str(MESH), export_format="GLB", use_selection=True,
                                 use_active_scene=True) == {"FINISHED"}
data = fixture.data
bpy.data.objects.remove(fixture, do_unlink=True)
bpy.data.meshes.remove(data)
SPLAT = OUTPUT / "splats.npz"
index = np.arange(1024, dtype=np.float32) + 0.5
z = 1-2*index/len(index)
radius = np.sqrt(1-z*z)
angle = index*2.399963
positions = np.stack((radius*np.cos(angle), radius*np.sin(angle), z), axis=1)
colors = np.stack((0.48+0.42*np.sin(positions[:, 2]*3),
                   0.48+0.40*np.sin(positions[:, 0]*2+1),
                   0.48+0.40*np.cos(positions[:, 1]*3)), axis=1)
save_splats(SPLAT, {"positions": positions.astype(np.float32),
    "scales": np.full((len(index), 3), 0.075, np.float32),
    "rotations": np.tile(np.array([1, 0, 0, 0], np.float32), (len(index), 1)),
    "opacities": np.full(len(index), 0.85, np.float32),
    "sh_dc": ((colors-0.5)/0.28209479177387814).astype(np.float32)})
hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (MESH, SPLAT)}
step = waits = 0
result_id = None
freeze = None
documents = None
source_position = [0.0, 100.0]
running_capture = False


def center(identifier):
    x, y, w, h = canvas.BOARD.card_rect(identifier)
    return x+w/2, y+h/2


def mouse(kind, value, point, **modifiers):
    region = canvas.window_region(canvas.board_area(canvas.BOARD))
    window.event_simulate(type=kind, value=value, x=int(region.x+point[0]), y=int(region.y+point[1]), **modifiers)


def drag_to(target, **modifiers):
    board = canvas.BOARD
    board.selected = {"splat"}
    mouse("MOUSEMOVE", "NOTHING", center("splat"), **modifiers)
    mouse("LEFTMOUSE", "PRESS", center("splat"), **modifiers)
    mouse("MOUSEMOVE", "NOTHING", center(target), **modifiers)


def assert_unchanged():
    board = canvas.BOARD
    assert board.catalog.assets["splat"].document() == documents[0]
    assert board.catalog.assets["mesh"].document() == documents[1]
    assert board.positions["splat"] == source_position
    assert set(scene.objects.keys()) == original_objects, "Texture drop imported objects into the working scene"
    assert not board.loaded_previews, "The animation loaded a live Gaussian renderer"
    for path, digest in hashes.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, "Texture drop overwrote an input file"


def advance():
    global step, waits, result_id, freeze, documents, running_capture
    waits += 1
    assert waits < 600, f"Timed out at step {step}"
    board = canvas.BOARD
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        if board is None:
            return 0.2
        board.catalog.assets["splat"] = Asset("splat", "SPLAT", artifact=str(SPLAT), title="Chromatic splats")
        board.catalog.assets["mesh"] = Asset("mesh", "MESH", artifact=str(MESH), title="Sphere")
        board.order = ["splat", "mesh"]
        board.positions = {"splat": list(source_position), "mesh": [390.0, 100.0]}
        board.scales = {"splat": 0.9, "mesh": 0.9}
        board.zoom, board.pan = 0.8, [70.0, 280.0]
        canvas.save_layout(bpy.context)
    elif step == 2:
        if board.texture("splat") is None or board.texture("mesh") is None:
            assert not board.preview_errors, board.preview_errors
            return 0.2
        documents = [board.catalog.assets[key].document() for key in ("splat", "mesh")]
        freeze = mock.patch.object(board.runner, "tick", return_value=False)
        freeze.start()
        source = board.catalog.assets["splat"]
        source.artifact = str(OUTPUT / "missing.npz")
        try:
            canvas.queue_texture(bpy.context, "mesh", "splat")
        except ValueError as exc:
            assert "missing" in str(exc)
        else:
            raise AssertionError("A missing source queued a pretend bake")
        finally:
            source.artifact = str(SPLAT)
        assert not board.texture_effects and len(board.catalog.assets) == 2
        board.card_drag = {"path": "splat", "moved": True}
        board.selected = {"splat", "mesh"}
        assert canvas.texture_drop_target(board, *center("mesh")) is None, "Group movement triggered a bake"
        board.card_drag = None
        drag_to("mesh", ctrl=True)
        # Releasing Ctrl before the mouse must not turn a move into a bake.
        mouse("LEFTMOUSE", "RELEASE", center("mesh"), ctrl=False)
    elif step == 3:
        assert len(board.catalog.assets) == 2 and board.texture_drop is None, "Ctrl-drag started a bake"
        assert board.positions["splat"] != source_position, "Ctrl-drag did not move the card normally"
        board.positions["splat"] = list(source_position)
        drag_to("mesh")
    elif step == 4:
        assert board.texture_drop is not None and board.texture_drop["target"] == "mesh"
        # Drawing feedback must be entirely cached, with no renderer construction.
        with mock.patch.object(canvas, "Preview", side_effect=AssertionError("Rendered during drop feedback")):
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / "hover.png"))
        capture = bpy.data.images.load(str(OUTPUT / "hover.png"), check_existing=False)
        region = canvas.window_region(canvas.board_area(board))
        x, y, w, h = board.card_rect("mesh")
        pixel = (int(region.y+y+h/2)*capture.size[0] + int(region.x+x+w/4))*4
        assert capture.pixels[pixel+1] < 0.65, "Drop feedback obscured the mesh with an opaque green panel"
        bpy.data.images.remove(capture)
        point = center("mesh")
        region = canvas.window_region(canvas.board_area(board))
        window.event_simulate(type="ESC", value="PRESS", x=int(region.x+point[0]), y=int(region.y+point[1]))
    elif step == 5:
        assert board.card_drag is None and board.texture_drop is None and len(board.catalog.assets) == 2
        assert_unchanged()
        drag_to("mesh")
    elif step == 6:
        assert board.texture_drop is not None
        mouse("LEFTMOUSE", "RELEASE", center("mesh"))
    elif step == 7:
        assert len(board.catalog.assets) == 3
        result_id = next(key for key in board.catalog.assets if key not in {"splat", "mesh"})
        result = board.catalog.assets[result_id]
        assert result.operation == "TEXTURE" and result.state == "QUEUED" and result.parent == "mesh"
        assert result.links["texture_source"] == "splat" and result.settings["mesh_artifact"] == str(MESH)
        assert result_id in board.texture_effects and result.preview == ""
        assert_unchanged()
        drag_to("mesh")
    elif step == 8:
        mouse("LEFTMOUSE", "RELEASE", center("mesh"))
    elif step == 9:
        assert len(board.catalog.assets) == 3 and board.selected == {result_id}, "Repeated drop queued a duplicate bake"
        assert_unchanged()
        freeze.stop()
        freeze = None
    elif step == 10:
        result = board.catalog.assets[result_id]
        if result.state in {"QUEUED", "RUNNING"}:
            if result.state == "RUNNING" and not running_capture:
                bpy.ops.screen.screenshot(filepath=str(OUTPUT / "transferring.png"))
                running_capture = True
            return 0.2
        assert result.state == "READY", result.status
        if board.texture(result_id) is None:
            return 0.2
        assert_unchanged()
        assert running_capture
        glb = Path(result.artifact).read_bytes()
        length, kind = struct.unpack_from("<II", glb, 12)
        document = json.loads(glb[20:20+length])
        assert kind == 0x4E4F534A and len(document.get("images", [])) >= 2
        assert all("bufferView" in image for image in document["images"]), "Textures were not embedded"
        assert "normalTexture" in document["materials"][0]
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "textured.png"))
    elif step == 11:
        # Success feedback expires and the idle board stops its animation timer.
        if result_id in board.texture_effects:
            return 0.2
        assert canvas.board_tick() == 0.1
        # Failed/cancelled jobs must not play the green completion burst.
        result = board.catalog.assets[result_id]
        for state in ("ERROR", "CANCELLED"):
            result.state = state
            board.texture_effects[result_id] = {"source": "splat", "target": "mesh", "started": 0, "finished": None}
            texture_effects.update(board, 10)
            assert board.texture_effects[result_id]["finished"] == 10
            texture_effects.update(board, 10.5)
            assert result_id not in board.texture_effects
        result.state = "READY"
        saved = canvas.load_layout(bpy.context)
        restored = next(item for item in saved["assets"] if item["id"] == result_id)
        assert restored["links"]["texture_source"] == "splat"
        remi.unregister()
        (OUTPUT / "passed.txt").write_text("Body drop, cached hover feedback, Ctrl move-only, Escape, source restoration, duplicate protection, real texture bake with embedded albedo/normals, idle/error/cancel animation cleanup and persistence passed.\n")
        print(f"REMI_BOARD_TEXTURE_DROP_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return 0.35


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        print(f"REMI_BOARD_TEXTURE_DROP_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=0.6)
