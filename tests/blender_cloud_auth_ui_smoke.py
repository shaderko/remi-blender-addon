"""Real board-header/account popover rendering; no network or account required."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.mkdtemp(prefix="remi-cloud-auth-ui-"))
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
bpy.context.scene.remi_image.output = str(OUTPUT / "images")
bpy.context.scene.remi_generation.output = str(OUTPUT / "models")
bpy.context.preferences.filepaths.temporary_directory = str(OUTPUT)
from remi.features.board import canvas

step = 0
waits = 0


def advance():
    global step, waits
    try:
        if step == 0:
            assert bpy.ops.remi_board.open() == {"FINISHED"}
        elif step == 1:
            board = canvas.BOARD
            area = canvas.board_area(board) if board is not None else None
            if area is None:
                waits += 1
                assert waits < 25, "Board workspace did not open"
                return .2
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / "board-header.png"))
            region = next(r for r in area.regions if r.type == "HEADER")
            bpy.context.window.cursor_warp(region.x + 290, region.y + region.height // 2)
            with bpy.context.temp_override(area=area, region=region):
                result = bpy.ops.wm.call_panel("INVOKE_DEFAULT", name="Remi_PT_BoardCloud", keep_open=True)
                assert result in ({"FINISHED"}, {"RUNNING_MODAL"}, {"INTERFACE"}), result
        elif step == 2:
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / "cloud-account.png"))
            assert not bpy.context.window_manager.remi_cloud.signed_in
            assert bpy.context.scene.remi_image is not None
            print(f"REMI_CLOUD_AUTH_UI_SMOKE_OK: {OUTPUT}", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 1.0
    except Exception:
        traceback.print_exc()
        os._exit(1)


bpy.app.timers.register(advance, first_interval=1.0)
