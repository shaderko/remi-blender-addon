"""Right-click naming must invoke an editable dialog, not execute an empty name.

Run with --factory-startup --enable-event-simulate. REMI_NAMING_MENU_INSTALLED=1
exercises the enabled installed extension instead of registering the checkout.
"""

from datetime import datetime
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_naming_menu_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
if os.environ.get("REMI_NAMING_MENU_INSTALLED") == "1":
    package = "bl_ext.user_default.remi"
    assert bpy.ops.preferences.addon_enable(module=package) == {"FINISHED"}
    remi = importlib.import_module(package)
else:
    package = "remi"
    spec = importlib.util.spec_from_file_location(package, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules[package] = remi
    spec.loader.exec_module(remi)
    remi.register()
canvas = importlib.import_module(package + ".features.board.canvas")
organizing = importlib.import_module(package + ".features.board.organizing")
Asset = importlib.import_module(package + ".features.board.assets").Asset
group_bounds = importlib.import_module(package + ".features.board.organization").group_bounds

window, scene = bpy.context.window, bpy.context.scene
scene.remi_image.output = str(OUTPUT / "images")
scene.remi_generation.output = str(OUTPUT / "models")
image = bpy.data.images.new("Menu naming reference", 64, 64)
image.generated_color = (.08, .35, .6, 1)
image.filepath_raw, image.file_format = str(OUTPUT / "reference.png"), "PNG"
image.save()
bpy.data.images.remove(image)

# Menu item indices are local to draw_actions, shared by all three menus.
cases = (("card", 0, "Ocean Reference"), ("card", 1, "References"),
         ("group", 0, "Reference Group"), ("group", 1, "Library"),
         ("background", 0, "Asset Library"), ("background", 1, "Project"))
step = waits = case = 0
cursor = (0, 0)
before_groups = set()
target = ""
cancel_case = len(cases)


def mouse(kind, value, x, y):
    global cursor
    region = canvas.window_region(canvas.board_area(canvas.BOARD))
    cursor = int(region.x+x), int(region.y+y)
    window.event_simulate(type=kind, value=value, x=cursor[0], y=cursor[1])


def key(kind, **kwargs):
    window.event_simulate(type=kind, value="PRESS", x=cursor[0], y=cursor[1], **kwargs)
    window.event_simulate(type=kind, value="RELEASE", x=cursor[0], y=cursor[1])


def advance():
    global step, waits, case, before_groups, target
    waits += 1
    assert waits < 100, f"Timed out at step {step} case {case}"
    board = canvas.BOARD
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        if board is None:
            return .2
        for index, identifier in enumerate(("reference", "detail")):
            asset = Asset(identifier, "IMAGE", artifact=str(OUTPUT / "reference.png"), title="Original image prompt")
            board.catalog.assets[identifier] = asset
            board.positions[identifier] = [100+index*340, 100]
            board.scales[identifier] = .6
            board.order.append(identifier)
        board.zoom, board.pan = .8, [100, 240]
        board.focused = False
        board.selected = {"reference", "detail"}
    else:
        phase = (step-2) % 4
        if phase == 0:
            before_groups = set(board.organization.groups)
            if case == cancel_case:
                kind, index, name = "card", 0, ""
            else:
                kind, index, name = cases[case]
            if kind == "card":
                x, y, w, h = board.card_rect("reference")
                x, y = x+w/2, y+h/2
                target = "reference"
            elif kind == "group":
                target = next(iter(board.group_selected))
                x, y, w, h = organizing.screen_rect(board, group_bounds(board)[target])
                x, y = x+w/2, y+h-max(18, 38*board.zoom)/2
            else:
                target = next(iter(board.group_selected))
                x, y = board.region[0]-80, board.region[1]-80
                assert board.hit(x, y) is None and organizing.header_hit(board, x, y) is None
            mouse("MOUSEMOVE", "NOTHING", x, y)
            mouse("RIGHTMOUSE", "PRESS", x, y)
            mouse("RIGHTMOUSE", "RELEASE", x, y)
        elif phase == 1:
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / f"menu-{case}.png"))
            key("HOME")
            index = 0 if case == cancel_case else cases[case][1]
            for _ in range(index):
                key("DOWN_ARROW")
            key("RET")
        elif phase == 2:
            assert set(board.organization.groups) == before_groups, "Menu action executed before asking for a name"
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / f"name-field-{case}.png"))
            if case == cancel_case:
                key("ESC")
            else:
                key("A", ctrl=True)
                for char in cases[case][2]:
                    kind = "SPACE" if char == " " else char.upper()
                    key(kind, unicode=char)
                key("RET")
                key("RET")
        else:
            if case == cancel_case:
                assert board.catalog.assets["reference"].label == "Ocean Reference"
                assert set(board.organization.groups) == before_groups
                if os.environ.get("REMI_NAMING_MENU_INSTALLED") == "1":
                    assert bpy.ops.preferences.addon_disable(module=package) == {"FINISHED"}
                else:
                    remi.unregister()
                print(f"REMI_BOARD_NAMING_MENU_SMOKE_OK: {OUTPUT}", flush=True)
                bpy.ops.wm.quit_blender()
                return None
            kind, index, name = cases[case]
            if index == 0:
                if kind == "card":
                    assert board.catalog.assets[target].label == name
                    assert board.catalog.assets[target].title == "Original image prompt"
                else:
                    assert board.organization.groups[target].title == name
                assert set(board.organization.groups) == before_groups
            else:
                created = set(board.organization.groups)-before_groups
                assert len(created) == 1
                group = board.organization.groups[next(iter(created))]
                assert group.title == name and board.group_selected == {group.id}
            case += 1
    step += 1
    waits = 0
    return .4


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        print(f"REMI_BOARD_NAMING_MENU_SMOKE_FAILED step {step} case {case}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=.6)
