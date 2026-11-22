"""Real display scaling: composer input, tool hits, dialogs and GPU state."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
import gpu

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.mkdtemp(prefix="remi-dpi-ui-"))
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
from remi.features.board import canvas, settings_window, toolbar
from remi.features.board.ui_metrics import ui_scale, logical_pointer

bpy.context.scene.remi_image.output = str(OUTPUT / "images")
bpy.context.scene.remi_generation.output = str(OUTPUT / "models")
bpy.context.preferences.filepaths.temporary_directory = str(OUTPUT)
original_scale = bpy.context.preferences.view.ui_scale
scales = (.5, .625, .75, 1, 1.5)
step = 0
index = 0
report = []


def mouse(kind, value, x, y):
    region = canvas.window_region(canvas.board_area(canvas.BOARD))
    bpy.context.window.event_simulate(type=kind, value=value,
                                    x=int(region.x+x), y=int(region.y+y))


def click(x, y, kind="LEFTMOUSE"):
    mouse("MOUSEMOVE", "NOTHING", x, y)
    mouse(kind, "PRESS", x, y)
    mouse(kind, "RELEASE", x, y)


def action_point(kind, target=None):
    host = settings_window.active_window
    for (x, y, w, h), action in host.actions:
        if action[0] == kind and (target is None or action[1] == target):
            return x+w/2, y+h/2
    raise AssertionError((kind,target,host.actions))


def advance():
    global step, index
    try:
        if step == 0:
            bpy.ops.remi_board.open()
        elif step == 1:
            if canvas.BOARD is None: return .2
            area = canvas.board_area(canvas.BOARD)
            with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
                bpy.ops.screen.screen_full_area()
            bpy.context.preferences.view.ui_scale = scales[index]
        elif step == 2:
            board = canvas.BOARD
            area = canvas.board_area(board)
            region = canvas.window_region(area)
            factor = ui_scale()
            assert abs(board.region[0]*factor-canvas.board_view_size(area)[0]) < 1
            assert abs(board.region[1]*factor-region.height) < 1
            x,y,w,h = board.bar_rect
            px,py = (x+w/2)*factor,(y+h/2)*factor
            logical = logical_pointer(region.x+px,region.y+py,region,factor)
            assert abs(logical[0]-(x+w/2)) < .001
            (OUTPUT / "pointer.json").write_text(json.dumps({"scale":factor,"bar":board.bar_rect,"point":[px,py],"region":[region.x,region.y,region.width,region.height]}))
            click(px,py,"RIGHTMOUSE")
        elif step == 3:
            host = settings_window.active_window
            assert host is not None and host.section == "image"
            assert len([a for _,a in host.actions if a[0] == "section"]) == 7
            click(*action_point("section","general"))
        elif step == 4:
            host = settings_window.active_window
            assert host.section == "general"
            mode = bpy.context.scene.remi_generation.backend
            host.expected_mode = "LOCAL" if mode == "API" else "API"
            for (x,y,w,h),action in host.actions:
                if action[0] == "property" and action[1].name == "use_cloud":
                    click(x+w/2,y+h/2)
                    break
            else: raise AssertionError("Missing Cloud checkbox")
        elif step == 5:
            host = settings_window.active_window
            assert bpy.context.scene.remi_generation.backend == host.expected_mode
            factor = ui_scale()
            region = canvas.window_region(canvas.board_area(canvas.BOARD))
            coordinates = [(rect[0],rect[1],rect[2],rect[3]) for rect,_ in host.actions]
            report.append({"requested":scales[index],"effective":factor,
                           "region":[region.width,region.height],
                           "composer_pixels":[v*factor for v in canvas.BOARD.bar_rect],
                           "actions":coordinates})
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / f"settings-{index}.png"))
            click(*action_point("close"))
        elif step == 6:
            assert settings_window.active_window is None
            tool, (x,y,w,h) = next(pair for pair in toolbar.buttons(canvas.BOARD) if pair[0].id == "generate3d")
            factor = ui_scale()
            click((x+w/2)*factor,(y+h/2)*factor,"RIGHTMOUSE")
        elif step == 7:
            assert settings_window.active_window is not None
            assert settings_window.active_window.section == "generate3d"
            settings_window.active_window.finish()
            area = canvas.board_area(canvas.BOARD)
            with bpy.context.temp_override(area=area,region=canvas.window_region(area)):
                before = gpu.matrix.get_model_view_matrix().copy()
                try:
                    with canvas.scaled_drawing(ui_scale()):
                        raise RuntimeError("Injected draw failure")
                except RuntimeError: pass
                assert gpu.matrix.get_model_view_matrix() == before
                assert canvas._DRAW_SCALE == 1
            index += 1
            if index < len(scales):
                bpy.context.preferences.view.ui_scale = scales[index]
                step = 2
                return .8
            assert len({entry["effective"] for entry in report}) == len(scales), report
            bpy.context.preferences.view.ui_scale = original_scale
            (OUTPUT / "report.json").write_text(json.dumps(report,indent=2))
            (OUTPUT / "passed.txt").write_text("Real DPI changes, composer/tool hits, settings navigation, checkbox input, GPU restoration.")
            print(f"REMI_DPI_SMOKE_OK: {OUTPUT}",flush=True)
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return .8
    except Exception:
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failure.png"))
        (OUTPUT / "error.txt").write_text(traceback.format_exc())
        traceback.print_exc()
        os._exit(1)


bpy.app.timers.register(advance,first_interval=.5)
