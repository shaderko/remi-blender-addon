"""Real board-header/account popover rendering; no network or account required."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

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
from remi.features.cloud import tick
from remi.features.cloud import sign_in

browser = patch.object(sign_in.webbrowser, "open", return_value=True).start()
patch.object(sign_in.runtime, "status", side_effect=lambda: {
    "state": "approval",
    "user_code": bpy.context.window_manager.remi_cloud.user_code,
    "verification_url": bpy.context.window_manager.remi_cloud.verification_url,
}).start()

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
            # Preview browser approval without starting a network request.
            bpy.app.timers.unregister(tick)
            state = bpy.context.window_manager.remi_cloud
            state.busy = True
            bpy.context.window.cursor_warp(region.x + 140, region.y + region.height // 2)
            with bpy.context.temp_override(area=area, region=region):
                result = bpy.ops.remi.cloud_account("INVOKE_DEFAULT")
                assert result in ({"FINISHED"}, {"RUNNING_MODAL"}, {"INTERFACE"}), result
        elif step == 2:
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / "cloud-connecting.png"))
            state = bpy.context.window_manager.remi_cloud
            state.user_code = "ABCD2345"
            state.verification_url = "https://remi3d.app/auth/device?user_code=ABCD2345"
            canvas.board_area(canvas.BOARD).tag_redraw()
        elif step == 3:
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / "cloud-account.png"))
            assert not bpy.context.window_manager.remi_cloud.signed_in
            assert bpy.context.scene.remi_image is not None
            browser.assert_called_once_with(bpy.context.window_manager.remi_cloud.verification_url, new=2)
            state = bpy.context.window_manager.remi_cloud
            state.user_code = ""
            state.busy = False
            state.signed_in = True
            state.email = "artist@example.com"
            canvas.board_area(canvas.BOARD).tag_redraw()
        elif step == 4:
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / "cloud-signed-in.png"))
            assert sign_in.active_dialog is None
            (OUTPUT / "passed.txt").write_text("Centered sign-in, browser opened once, automatic close.")
            print(f"REMI_CLOUD_AUTH_UI_SMOKE_OK: {OUTPUT}", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 1.0
    except Exception:
        (OUTPUT / "error.txt").write_text(traceback.format_exc())
        traceback.print_exc()
        os._exit(1)


bpy.app.timers.register(advance, first_interval=1.0)
