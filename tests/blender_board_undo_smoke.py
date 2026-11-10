"""Undo across opening the board must not detach its workspace or live queue."""

from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_board_undo_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, workspace
from remi.features.board.assets import Asset


window = bpy.context.window
bpy.context.scene.remi_image.output = str(OUTPUT / "images")
image = bpy.data.images.new("Undo fixture", 8, 8)
image.filepath_raw = str(OUTPUT / "fixture.png")
image.file_format = "PNG"
image.save()
bpy.data.images.remove(image)
patcher = mock.patch.object(canvas, "start_next")
patcher.start()
step = waits = 0
board = None
host_name = None
workspace_count = None
expected_position = [120.0, -75.0]


def simulate(key, *, ctrl=False, shift=False, area=None):
    area = area or next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region = canvas.window_region(area)
    x, y = region.x + region.width // 2, region.y + region.height // 2
    window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
    if key == "MOUSEMOVE":
        return
    window.event_simulate(type=key, value="PRESS", unicode="1" if key == "ONE" else "", ctrl=ctrl, shift=shift, x=x, y=y)
    window.event_simulate(type=key, value="RELEASE", ctrl=ctrl, shift=shift, x=x, y=y)


def assert_board():
    assert canvas.BOARD is board, "Undo replaced the board and lost its live job queue"
    assert board.queue == ["Must survive native Undo"]
    assert board.catalog.assets["undo-card"].title == "Keep this card"
    assert board.positions["undo-card"] == expected_position
    assert canvas.board_area(board) is not None, "Undo detached the board's editor space"
    assert window.workspace.get(workspace.WORKSPACE_KEY), "Undo removed the workspace identity"
    assert len(bpy.data.workspaces) == workspace_count, "Reopening created a duplicate workspace"
    assert sum(op.bl_rna.identifier == "REMI_BOARD_OT_run" for op in window.modal_operators) == 1, "Undo removed the canvas input handler"


def advance():
    global step, waits, board, workspace_count, host_name, expected_position
    waits += 1
    assert waits < 100, f"Timed out at step {step}"
    if step == 0:
        bpy.ops.ed.undo_push(message="Before opening Remi")
        bpy.data.objects["Cube"].location.x = 1
        bpy.ops.ed.undo_push(message="User mesh change")
    elif step == 1:
        assert abs(bpy.data.objects["Cube"].location.x - 1) < 1e-5
        assert bpy.ops.remi_board.open() == {"FINISHED"}
    elif step == 2:
        if canvas.BOARD is None:
            return 0.2
        board = canvas.BOARD
        host_name = window.workspace.name
        workspace_count = len(bpy.data.workspaces)
        asset = Asset("undo-card", "MESH", title="Keep this card", artifact=str(OUTPUT / "missing.glb"),
                      preview=str(OUTPUT / "fixture.png"))
        board.catalog.assets[asset.id] = asset
        board.order.append(asset.id)
        board.positions[asset.id] = [120.0, -75.0]
        board.add_pending("Must survive native Undo")
        board.queue.append("Must survive native Undo")
        canvas.save_layout(bpy.context)
        simulate("Z", ctrl=True)
    elif step == 3:
        assert abs(bpy.data.objects["Cube"].location.x) < 1e-5
        assert_board()
        assert bpy.ops.remi_board.open() == {"FINISHED"}
    elif step == 4:
        assert_board()
        simulate("Z", ctrl=True, shift=True)
    elif step == 5:
        assert abs(bpy.data.objects["Cube"].location.x - 1) < 1e-5
        assert_board()
        area = canvas.board_area(board)
        with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
            bpy.ops.screen.screen_full_area()
    elif step == 6:
        assert window.screen.show_fullscreen
        simulate("Z", ctrl=True, area=window.screen.areas[0])
    elif step == 7:
        assert abs(bpy.data.objects["Cube"].location.x) < 1e-5
        assert_board()
        assert bpy.ops.remi_board.open() == {"FINISHED"}
    elif step == 8:
        assert_board()
        assert len(window.screen.areas) == 1
        with bpy.context.temp_override(area=window.screen.areas[0]):
            bpy.ops.screen.screen_full_area()
    elif step == 9:
        # Losing the UI tags should be recoverable too, without a second canvas.
        for key in (workspace.WORKSPACE_KEY, workspace.LAYOUT_KEY, workspace.HOST_KEY):
            window.workspace.pop(key, None)
        board.space = 0
        board.workspace = 0
        assert bpy.ops.remi_board.open() == {"FINISHED"}
    elif step == 10:
        assert_board()
        assert len([area for area in window.screen.areas if area.type == "IMAGE_EDITOR"]) == 1
        # Simulate a native modal handler being invalidated while its Python
        # board/job state survives. The monitor must reconnect input only.
        operator = next(op for op in window.modal_operators if op.bl_rna.identifier == "REMI_BOARD_OT_run")
        operator._board = None
        simulate("MOUSEMOVE", area=canvas.board_area(board))
    elif step == 11:
        assert_board()
        bpy.ops.ed.undo_push(message="Before scene rename")
        window.scene.name = "Temporary board scene name"
        bpy.ops.ed.undo_push(message="Rename board scene")
        simulate("Z", ctrl=True)
    elif step == 12:
        assert window.scene.name == "Scene"
        assert_board()
        window.workspace = bpy.data.workspaces["Layout"]
    elif step == 13:
        area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
        with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
            bpy.ops.screen.screen_full_area()
    elif step == 14:
        assert window.screen.show_fullscreen
        assert bpy.ops.remi_board.open() == {"FINISHED"}
    elif step == 15:
        if canvas.board_area(board) is None:
            return 0.2
        assert_board()
        assert bpy.ops.workspace.delete() == {"FINISHED"}
    elif step == 16:
        assert len(bpy.data.workspaces) == workspace_count - 1
        assert canvas.BOARD is board and board.queue == ["Must survive native Undo"]
        assert bpy.ops.remi_board.open() == {"FINISHED"}
    elif step == 17:
        if canvas.board_area(board) is None:
            return 0.2
        assert_board()
        area = canvas.board_area(board)
        region = canvas.window_region(area)
        x, y, w, h = board.card_rect("undo-card")
        x, y = int(region.x + x + w / 2), int(region.y + y + h / 2)
        for kind, value, px, py in (("MOUSEMOVE", "NOTHING", x, y),
                                   ("LEFTMOUSE", "PRESS", x, y),
                                   ("MOUSEMOVE", "NOTHING", x + 40, y + 20),
                                   ("LEFTMOUSE", "RELEASE", x + 40, y + 20)):
            window.event_simulate(type=kind, value=value, x=px, y=py)
        expected_position = [120 + 40 / board.zoom, -75 + 20 / board.zoom]
    elif step == 18:
        assert_board()
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "restored.png"))
        patcher.stop()
        board.queue.clear()
        remi.unregister()
        print(f"REMI_BOARD_UNDO_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return 0.6


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        print(f"REMI_BOARD_UNDO_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=0.5)
