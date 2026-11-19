"""Real N-panel clicks/search, distant layer navigation and saved native UI.

Run with --factory-startup --enable-event-simulate. REMI_LAYERS_INSTALLED=1
tests the enabled installed extension instead of the checkout. No inference.
"""

from copy import deepcopy
from datetime import datetime
import importlib
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
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_layers_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
installed = os.environ.get("REMI_LAYERS_INSTALLED") == "1"
if installed:
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
layers = importlib.import_module(package + ".features.board.layers")
organizing = importlib.import_module(package + ".features.board.organizing")
organization = importlib.import_module(package + ".features.board.organization")
Asset = importlib.import_module(package + ".features.board.assets").Asset

window, scene = bpy.context.window, bpy.context.scene
scene.remi_image.output = str(OUTPUT / "images")
scene.remi_generation.output = str(OUTPUT / "models")
for index, color in enumerate(((.08, .35, .65, 1), (.6, .2, .07, 1))):
    job = OUTPUT / "images" / str(index)
    job.mkdir(parents=True)
    image = bpy.data.images.new("Layer reference", 64, 64)
    image.generated_color = color
    image.filepath_raw, image.file_format = str(job / "image_0.png"), "PNG"
    image.save()
    bpy.data.images.remove(image)
    (job / "meta.json").write_text(json.dumps({"prompt": f"Original prompt {index}", "images": ["image_0.png"]}))
    (job / "complete.json").write_text("{}")

step = waits = 0
cursor = (0, 0)
child = parent = ocean = ""
positions = documents = view = None
freeze = None
objects = set(scene.objects.keys())


def ui_region():
    return next(region for region in canvas.board_area(canvas.BOARD).regions if region.type == "UI")


def mouse(kind, value, x, y, **kwargs):
    global cursor
    cursor = int(x), int(y)
    window.event_simulate(type=kind, value=value, x=cursor[0], y=cursor[1], **kwargs)


def click(x, y, **kwargs):
    if kwargs.get("shift"):
        mouse("LEFT_SHIFT", "PRESS", x, y, shift=True)
    mouse("MOUSEMOVE", "NOTHING", x, y, **kwargs)
    # Let Blender activate the native button under the new pointer first.
    def press():
        mouse("LEFTMOUSE", "PRESS", x, y, **kwargs)
        mouse("LEFTMOUSE", "RELEASE", x, y, **kwargs)
        if kwargs.get("shift"):
            def release_shift():
                mouse("LEFT_SHIFT", "RELEASE", x, y)
            bpy.app.timers.register(release_shift, first_interval=.08)
    bpy.app.timers.register(press, first_interval=.08)


def key(kind, **kwargs):
    window.event_simulate(type=kind, value="PRESS", x=cursor[0], y=cursor[1], **kwargs)
    window.event_simulate(type=kind, value="RELEASE", x=cursor[0], y=cursor[1])


def layer_click(index, *, arrow=False, **kwargs):
    region = ui_region()
    click(region.x+(22 if arrow else region.width/2), region.y+region.height-74-index*30, **kwargs)


def search_click():
    region = ui_region()
    click(region.x+90, region.y+region.height-44)


def advance():
    global step, waits, child, parent, ocean, positions, documents, view, freeze, window, scene
    waits += 1
    assert waits < 130, f"Timed out at step {step}"
    board = canvas.BOARD
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        if board is None or len(board.order) != 2:
            return .2
        freeze = mock.patch.object(board.runner, "tick", return_value=False)
        freeze.start()
        ocean, detail = board.order
        board.catalog.assets[ocean].label = "Ocean reference"
        board.catalog.assets[detail].label = "Hull reference"
        preview = board.catalog.assets[ocean].artifact
        for asset in (Asset("mesh", "MESH", artifact=str(OUTPUT / "mesh.glb"), title="Ship mesh", preview=preview, preview_view=[0, 20]),
                      Asset("queued", "SPLAT", artifact=str(OUTPUT / "source.npz"), title="Queued source", state="QUEUED")):
            board.catalog.assets[asset.id] = asset
            board.order.append(asset.id)
        for index, identifier in enumerate(board.order):
            board.positions[identifier] = [12000+index*450, -8000]
        child = board.organization.create({ocean, detail}, title="References").id
        parent = board.organization.create(set(), {child}, "Ship Library").id
        board.add_pending("A new concept image")
        board.zoom, board.pan = .8, [100, 300]
        board.composer = "Keep my composer text"
        board.focused = False
        positions = deepcopy(board.positions)
        documents = deepcopy(board.catalog.documents())
        region = canvas.window_region(canvas.board_area(board))
        mouse("MOUSEMOVE", "NOTHING", region.x+region.width/2, region.y+region.height/2)
        key("N")
    elif step == 2:
        assert canvas.board_area(board).spaces.active.show_region_ui
        if ui_region().width < 50:
            return .2
        ui_region().active_panel_category = "Remi"
    elif step == 3:
        assert layers.thumbnail_icon(board.catalog.assets[ocean]) > 0
        assert layers.thumbnail_icon(board.catalog.assets["mesh"]) > 0
        assert board.region == canvas.board_view_size(canvas.board_area(board))
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "layers-tree.png"))
        view = (board.zoom, list(board.pan))
        layer_click(0, arrow=True)
    elif step == 4:
        assert board.layers_collapsed == {parent}, board.layers_collapsed
        assert (board.zoom, board.pan) == view and not board.selected
        search_click()
    elif step == 5:
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "search-field.png"))
        for char in "Ocean":
            key(char.upper(), unicode=char)
        key("RET")
    elif step == 6:
        assert bpy.context.window_manager.remi_board_layers.query == "Ocean", repr(bpy.context.window_manager.remi_board_layers.query)
        assert board.composer == "Keep my composer text" and not board.selected
        assert (board.zoom, board.pan) == view
        assert [row.id for row in organization.layer_rows(board, query="Ocean", collapsed=board.layers_collapsed)] == [parent, child, ocean]
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "search-collapsed.png"))
        layer_click(2)
    elif step == 7:
        assert board.selected == {ocean} and not board.group_selected
        x, y, w, h = board.card_rect(ocean)
        assert 75 <= x and x+w <= board.region[0]-20 and 145 <= y and y+h <= board.region[1]-20
        assert board.positions == positions
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "found-distant-card.png"))
        search_click()
    elif step == 8:
        key("A", ctrl=True)
        key("BACK_SPACE")
        key("RET")
    elif step == 9:
        assert not bpy.context.window_manager.remi_board_layers.query
        assert board.layers_collapsed == {parent}
        layer_click(0, arrow=True)
    elif step == 10:
        assert not board.layers_collapsed
        layer_click(1)
    elif step == 11:
        assert board.group_selected == {child} and len(board.selected) == 2
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "found-group.png"))
        layer_click(4, shift=True)
    elif step == 12:
        assert len(board.selected) == 3 and "mesh" in board.selected
        assert board.positions == positions and board.catalog.documents() == documents
        assert set(scene.objects.keys()) == objects
        layer_click(0, arrow=True)
    elif step == 13:
        assert board.layers_collapsed == {parent}
        canvas.save_layout(bpy.context)
        freeze.stop()
        freeze = None
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "layers.blend"))
        bpy.ops.wm.open_mainfile(filepath=str(OUTPUT / "layers.blend"))
    elif step == 14:
        window, scene = bpy.context.window, bpy.context.scene
        if board is None or canvas.board_area(board) is None:
            return .2
        assert board.layers_collapsed == {parent}
        assert canvas.board_area(board).spaces.active.show_region_ui
        assert board.positions[ocean] == positions[ocean]
        assert board.catalog.assets[ocean].label == "Ocean reference"
        assert board.catalog.assets[ocean].title.startswith("Original prompt")
        assert set(scene.objects.keys()) == objects
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "reopened-layers.png"))
        region = canvas.window_region(canvas.board_area(board))
        mouse("MOUSEMOVE", "NOTHING", region.x+100, region.y+region.height/2)
        board.focused = False
        key("N")
    elif step == 15:
        assert not canvas.board_area(board).spaces.active.show_region_ui
        if installed:
            assert bpy.ops.preferences.addon_disable(module=package) == {"FINISHED"}
        else:
            remi.unregister()
        print(f"REMI_BOARD_LAYERS_SMOKE_OK: {OUTPUT}", flush=True)
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
        print(f"REMI_BOARD_LAYERS_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=.6, persistent=True)
