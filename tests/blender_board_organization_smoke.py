"""Interactive shortcuts, nested headers, drag/cancel, viewport import and reload.

Run with --factory-startup --enable-event-simulate. No model inference.
"""

from copy import deepcopy
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_organization_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, organizing
from remi.features.board.organization import group_bounds


window, scene = bpy.context.window, bpy.context.scene
scene.remi_image.output = str(OUTPUT / "images")
scene.remi_generation.output = str(OUTPUT / "models")
for index, color in enumerate(((.08, .3, .6), (.45, .15, .08), (.12, .45, .25))):
    job = OUTPUT / "images" / str(index)
    job.mkdir(parents=True)
    image = bpy.data.images.new("Organization fixture", 256, 256)
    pixels = np.ones((256, 256, 4), dtype=np.float32)
    yy, xx = np.mgrid[0:256, 0:256]
    light = .4+.6*(xx+yy)/510
    for channel in range(3):
        pixels[:, :, channel] = color[channel]*light
    pixels[70:186, 70:186, :3] = color
    image.pixels.foreach_set(pixels.ravel())
    image.filepath_raw, image.file_format = str(job / "image_0.png"), "PNG"
    image.save()
    bpy.data.images.remove(image)
    (job / "meta.json").write_text(json.dumps({"prompt": f"Original prompt {index}", "images": ["image_0.png"]}))
    (job / "complete.json").write_text("{}")

step = waits = 0
keys = []
child = parent = imported = ""
before = saved = None
drag_point = None
cursor = (0, 0)


def point(x, y):
    region = canvas.window_region(canvas.board_area(canvas.BOARD))
    return int(region.x+x), int(region.y+y)


def mouse(kind, value, x, y):
    global cursor
    x, y = point(x, y)
    cursor = (x, y)
    window.event_simulate(type=kind, value=value, x=x, y=y)


def key(kind, ctrl=False, shift=False):
    x, y = cursor
    window.event_simulate(type=kind, value="PRESS", ctrl=ctrl, shift=shift, x=x, y=y)
    window.event_simulate(type=kind, value="RELEASE", ctrl=ctrl, shift=shift, x=x, y=y)


def header(board, identifier):
    x, y, w, h = organizing.screen_rect(board, group_bounds(board)[identifier])
    return x+w/2, y+h-max(18, 38*board.zoom)/2


def advance():
    global step, waits, keys, child, parent, imported, before, saved, drag_point, window, scene
    waits += 1
    assert waits < 150, f"Timed out at step {step}"
    board = canvas.BOARD
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        if board is None or len(board.order) < 3:
            return .2
        keys = list(board.order)
        for index, identifier in enumerate(keys):
            board.positions[identifier] = [index*350, 0]
        board.fit()
        board.selected = set(keys[:2])
        board.focused = False
        mouse("MOUSEMOVE", "NOTHING", board.region[0]/2, board.region[1]/2)
    elif step == 2:
        key("G", ctrl=True)
    elif step == 3:
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "group-dialog.png"))
        key("RET")
        key("RET")
    elif step == 4:
        assert len(board.organization.groups) == 1
        child = next(iter(board.group_selected))
        assert board.organization.members(child) == set(keys[:2])
        key("F2")
    elif step == 5:
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "rename-dialog.png"))
        key("A", ctrl=True)
        for char in "References":
            window.event_simulate(type=char.upper(), value="PRESS", unicode=char, x=cursor[0], y=cursor[1])
            window.event_simulate(type=char.upper(), value="RELEASE", x=cursor[0], y=cursor[1])
        key("RET")
        key("RET")
    elif step == 6:
        assert board.organization.groups[child].title == "References"
        board.selected.add(keys[2])
        key("G", ctrl=True)
    elif step == 7:
        key("RET")
        key("RET")
    elif step == 8:
        assert len(board.organization.groups) == 2
        parent = next(iter(board.group_selected))
        assert board.organization.groups[child].parent == parent
        bpy.ops.remi_board.rename(target=parent, name="Asset Library")
        bpy.ops.remi_board.rename(target=keys[0], name="Ocean reference")
        board.fit()
        before = deepcopy(board.positions)
        drag_point = header(board, parent)
        board.focused = True  # A header click must leave the composer.
        mouse("MOUSEMOVE", "NOTHING", *drag_point)
        mouse("LEFTMOUSE", "PRESS", *drag_point)
    elif step == 9:
        assert board.group_drag and not board.focused
        mouse("MOUSEMOVE", "NOTHING", drag_point[0]+30, drag_point[1]-20)
    elif step == 10:
        assert board.positions != before
        key("ESC")
        mouse("LEFTMOUSE", "RELEASE", drag_point[0]+30, drag_point[1]-20)
    elif step == 11:
        assert board.positions == before and board.group_drag is None
        drag_point = header(board, parent)
        mouse("MOUSEMOVE", "NOTHING", *drag_point)
        mouse("LEFTMOUSE", "PRESS", *drag_point)
        mouse("MOUSEMOVE", "NOTHING", drag_point[0]+30, drag_point[1]-20)
        mouse("LEFTMOUSE", "RELEASE", drag_point[0]+30, drag_point[1]-20)
    elif step == 12:
        assert board.positions != before and board.group_drag is None
        board.fit()
    elif step == 13:
        if any(board.texture(identifier) is None for identifier in keys):
            return .2
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "nested-groups.png"))
        board.zoom, board.pan = .7, [-9000, 4200]
        board.selected.clear()
        board.group_selected.clear()
        bpy.ops.object.select_all(action="DESELECT")
        source = scene.objects.get("Cube")
        source.select_set(True)
        bpy.context.view_layer.objects.active = source
        created = canvas.add_selected_objects(bpy.context)
        imported = created[0].id
        assert board.pan == [-9000, 4200]
        x, y, w, h = board.card_rect(imported)
        assert 80 <= x and x+w <= board.region[0]-24 and 150 <= y and y+h <= board.region[1]-24
    elif step == 14:
        if board.texture(imported) is None:
            assert not board.preview_errors, board.preview_errors
            return .2
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "current-view-import.png"))
        board.fit()
        canvas.save_layout(bpy.context)
        saved = json.loads(scene[canvas.SCENE_ASSETS_KEY])
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "organization.blend"))
        bpy.ops.wm.open_mainfile(filepath=str(OUTPUT / "organization.blend"))
    elif step == 15:
        window, scene = bpy.context.window, bpy.context.scene
        if board is None:
            return .2
        assert board.organization.documents() == saved["organization"]["groups"]
        assert board.organization.membership == saved["organization"]["membership"]
        assert board.catalog.assets[keys[0]].label == "Ocean reference"
        assert board.catalog.assets[keys[0]].title.startswith("Original prompt")
        assert sum(op.bl_rna.identifier == "REMI_BOARD_OT_run" for op in window.modal_operators) == 1
        board.zoom, board.pan = .55, [120, 290]
        organizing.select_group(board, parent)
        mouse("MOUSEMOVE", "NOTHING", *header(board, parent))
        key("G", ctrl=True, shift=True)
    elif step == 16:
        assert parent not in board.organization.groups and child in board.organization.groups
        assert all(not asset.hidden and Path(asset.artifact).is_file() for asset in board.catalog.assets.values())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "reopened-ungrouped.png"))
        remi.unregister()
        print(f"REMI_BOARD_ORGANIZATION_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return .5


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        print(f"REMI_BOARD_ORGANIZATION_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=.6, persistent=True)
