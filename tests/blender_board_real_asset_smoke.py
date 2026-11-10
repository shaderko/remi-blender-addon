"""Optional real artifact preview/legacy discovery check, with no inference.

blender --enable-event-simulate --python tests/blender_board_real_asset_smoke.py -- <generation>/splats.npz
Only copies source files into a new temporary test board; originals are read-only.
"""

from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from unittest import mock

import bpy

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = Path(sys.argv[sys.argv.index("--")+1])
METADATA = json.loads(ARCHIVE.with_name("meta.json").read_text(encoding="utf-8"))
SOURCE = Path(METADATA["image"])
DIGEST = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_real_board_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
images, models = OUTPUT / "images", OUTPUT / "models"
image_job, model_job = images / "source", models / "source"
image_job.mkdir(parents=True)
model_job.mkdir(parents=True)
image = image_job / ("image_000" + SOURCE.suffix)
shutil.copy2(SOURCE, image)
shutil.copy2(ARCHIVE, model_job / "splats.npz")
(image_job / "meta.json").write_text(json.dumps({"prompt": "Source image", "model": "upload", "images": [{"file": image.name}]}))
(image_job / "complete.json").write_text('{"schema": 1, "complete": true}')
(model_job / "meta.json").write_text(json.dumps({**METADATA, "image": str(image)}))
(model_job / "complete.json").write_text('{"schema": 1, "complete": true}')
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
from remi.features.board import canvas

bpy.context.scene.remi_image.output = str(images)
bpy.context.scene.remi_generation.output = str(models)
objects = set(bpy.data.objects.keys())
step = 0
waits = 0
asset_id = None


def advance():
    global step, waits, asset_id
    waits += 1
    assert waits < 100
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        board = canvas.BOARD
        if board is None:
            return 0.1
        assets = list(board.catalog.assets.values())
        assert len(assets) == 2, "Historical 3D output was not recovered"
        model = next(asset for asset in assets if asset.kind == "SPLAT")
        asset_id = model.id
        assert board.catalog.assets[model.parent].kind == "IMAGE"
        board.selected = {asset_id}
        area = canvas.board_area(board)
        with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
            bpy.ops.screen.screen_full_area()
    elif step == 2:
        if not canvas.BOARD.thumbnail_ready(canvas.BOARD.catalog.assets[asset_id]):
            assert asset_id not in canvas.BOARD.preview_errors, canvas.BOARD.preview_errors
            return 0.1
        assert not canvas.BOARD.loaded_previews, "Opening the board eagerly loaded a live preview"
        canvas.BOARD.fit()
    elif step == 3:
        assert set(bpy.data.objects.keys()) == objects
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "source_and_3d.png"))
        board = canvas.BOARD
        area = canvas.board_area(board)
        region = canvas.window_region(area)
        x, y, w, h = board.card_rect(asset_id)
        x, y = int(region.x+x+w/2), int(region.y+y+h/2)
        window = bpy.context.window
        window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
        window.event_simulate(type="LEFTMOUSE", value="PRESS", alt=True, x=x, y=y)
        window.event_simulate(type="MOUSEMOVE", value="NOTHING", alt=True, x=x+70, y=y+22)
    elif step == 4:
        asset = canvas.BOARD.catalog.assets[asset_id]
        assert 320 < asset.yaw < 340 and asset.elevation > 25, "Alt-drag horizontal orbit direction is incorrect"
        if asset_id not in canvas.BOARD.loaded_previews:
            return 0.1
        assert len(canvas.BOARD.loaded_previews) == 1
        assert canvas.BOARD.orbit_drag is not None
        assert canvas.BOARD.live_asset_id == asset_id
        bpy.context.window.event_simulate(type="LEFTMOUSE", value="RELEASE", alt=True, x=1100, y=500)
    elif step == 5:
        board = canvas.BOARD
        if board.live_asset_id:
            return 0.1
        assert board.orbit_drag is None
        assert board.thumbnail_ready(board.catalog.assets[asset_id])
        assert asset_id in board.loaded_previews
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "orbit.png"))
        used = board.loaded_previews[asset_id]["last_used"]
        board.expire_previews(now=used + 29.9)
        assert asset_id in board.loaded_previews, "Preview unloaded before its idle timeout"
        board.expire_previews(now=used + 30.1)
        assert not board.loaded_previews, "Idle native preview was not unloaded"
        # Both rendering and loading the 3D source are forbidden on the idle path.
        with mock.patch.object(canvas, "Preview", side_effect=AssertionError("Idle card rendered 3D")), \
             mock.patch.object(canvas, "NativePreview", side_effect=AssertionError("Idle card loaded a native renderer")), \
             mock.patch("remi.integrations.triposplat.splat_data.load", side_effect=AssertionError("Idle card loaded 3D")):
            board.prepare_preview()
            assert board.texture(asset_id) is not None, "Unloaded card lost its captured thumbnail"
        # Four real models: keep three, evict LRU, and reuse a recently orbited one.
        identifiers = []
        parent = board.catalog.assets[asset_id].parent
        for index in range(4):
            asset = board.catalog.derive(parent, "SPLAT", "GENERATE_3D")
            asset.state, asset.artifact, asset.yaw = "READY", str(model_job / "splats.npz"), index*10.0
            identifiers.append(asset.id)
            board.update_live_preview(asset.id)
            board.finish_live_preview()
            assert len(board.loaded_previews) <= 3
        assert len(board.loaded_previews) == 3 and identifiers[0] not in board.loaded_previews
        renderer = board.loaded_previews[identifiers[1]]["preview"]
        native_object = renderer.obj.as_pointer()
        native_screen = renderer.screen.as_pointer()
        with mock.patch("remi.integrations.triposplat.splat_data.load", side_effect=AssertionError("Warm preview reloaded 3D")):
            for yaw in (25.0, 35.0, 45.0):
                board.catalog.assets[identifiers[1]].yaw = yaw
                board.update_live_preview(identifiers[1])
                assert renderer.obj.as_pointer() == native_object
                assert renderer.screen.as_pointer() == native_screen
            board.finish_live_preview()
        asset = board.catalog.derive(parent, "SPLAT", "GENERATE_3D")
        asset.state, asset.artifact = "READY", str(model_job / "splats.npz")
        board.update_live_preview(asset.id)
        board.finish_live_preview()
        assert len(board.loaded_previews) == 3
        assert identifiers[1] in board.loaded_previews and identifiers[2] not in board.loaded_previews
        saved = OUTPUT / "no_preview_resources.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(saved))
        assert not board.loaded_previews, "Saving kept live preview resources"
        inspection = OUTPUT / "inspect_saved_preview_resources.blend"
        shutil.copy2(saved, inspection)
        with bpy.data.libraries.load(str(inspection), link=False) as (data_from, _data_to):
            assert not any("isolated native preview" in name for name in data_from.scenes)
            assert not any("temporary preview camera" in name for name in data_from.objects)
        assert not any(screen.get("remi_preview_screen") for screen in bpy.data.screens)
        assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == DIGEST
        assert set(bpy.data.objects.keys()) == objects
        remi.unregister()
        print(f"REMI_BOARD_REAL_ASSET_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return 0.5


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=0.5)
