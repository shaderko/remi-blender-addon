"""Board frame drops and native per-layer move menus, including saved nesting.

Run with --factory-startup --enable-event-simulate. REMI_GROUP_DROP_INSTALLED=1
uses the installed extension. No inference or texture bake is started.
"""

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_group_drop_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
installed = os.environ.get("REMI_GROUP_DROP_INSTALLED") == "1"
package = "bl_ext.user_default.remi" if installed else "remi"
if installed:
    assert bpy.ops.preferences.addon_enable(module=package) == {"FINISHED"}
    remi = importlib.import_module(package)
else:
    spec = importlib.util.spec_from_file_location(package, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules[package] = remi
    spec.loader.exec_module(remi)
    remi.register()
canvas = importlib.import_module(package + ".features.board.canvas")
organizing = importlib.import_module(package + ".features.board.organizing")
Asset = importlib.import_module(package + ".features.board.assets").Asset

window, scene = bpy.context.window, bpy.context.scene
scene.remi_image.output = str(OUTPUT / "images")
scene.remi_generation.output = str(OUTPUT / "models")
mesh = OUTPUT / "mesh.ply"
mesh.write_text("ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
                "element face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 2\n")
image = bpy.data.images.new("Group drop preview", 32, 32)
image.generated_color = (.09, .4, .65, 1)
image.filepath_raw, image.file_format = str(OUTPUT / "preview.png"), "PNG"
image.save()
bpy.data.images.remove(image)
hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (mesh, OUTPUT / "preview.png")}
objects = set(scene.objects.keys())
step = waits = 0
left = right = ""
cursor = (0, 0)
destination = None
positions = before_menu = saved = None


def mouse(kind, value, point, **kwargs):
    global cursor
    region = canvas.window_region(canvas.board_area(canvas.BOARD))
    cursor = int(region.x+point[0]), int(region.y+point[1])
    window.event_simulate(type=kind, value=value, x=cursor[0], y=cursor[1], **kwargs)


def key(kind):
    window.event_simulate(type=kind, value="PRESS", x=cursor[0], y=cursor[1])
    window.event_simulate(type=kind, value="RELEASE", x=cursor[0], y=cursor[1])


def center(identifier):
    x, y, w, h = canvas.BOARD.card_rect(identifier)
    return x+w/2, y+h/2


def header(identifier):
    board = canvas.BOARD
    rect = organizing.group_bounds(board)[identifier]
    x, y, w, h = organizing.screen_rect(board, rect)
    return x+w/2, y+h-max(18, 38*board.zoom)/2


def drag(start, end, **kwargs):
    global destination
    destination = end
    mouse("MOUSEMOVE", "NOTHING", start, **kwargs)
    mouse("LEFTMOUSE", "PRESS", start, **kwargs)
    mouse("MOUSEMOVE", "NOTHING", end, **kwargs)


def release():
    mouse("LEFTMOUSE", "RELEASE", destination)


def move_button(index):
    region = next(item for item in canvas.board_area(canvas.BOARD).regions if item.type == "UI")
    point = (region.x+region.width-47, region.y+region.height-74-index*30)
    window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=int(point[0]), y=int(point[1]))
    def click():
        global cursor
        cursor = int(point[0]), int(point[1])
        window.event_simulate(type="LEFTMOUSE", value="PRESS", x=cursor[0], y=cursor[1])
        window.event_simulate(type="LEFTMOUSE", value="RELEASE", x=cursor[0], y=cursor[1])
    bpy.app.timers.register(click, first_interval=.08)


def advance():
    global step, waits, left, right, positions, before_menu, saved, window, scene
    waits += 1
    assert waits < 120, f"Timed out at step {step}"
    board = canvas.BOARD
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        if board is None:
            return .2
        for index, identifier in enumerate(("a", "b", "c")):
            board.catalog.assets[identifier] = Asset(identifier, "MESH", artifact=str(mesh), title="Mesh " + identifier,
                preview=str(OUTPUT / "preview.png"), preview_view=[0, 20])
            board.positions[identifier] = [(100, 460, 100)[index], (220, 220, 0)[index]]
            board.scales[identifier] = .45
            board.order.append(identifier)
        left = board.organization.create({"a"}, title="Group A").id
        right = board.organization.create({"b"}, title="Group B").id
        board.zoom, board.pan = 1, [80, 200]
    elif step == 2:
        drag(center("c"), header(right))
    elif step == 3:
        assert board.group_drop["target"] == right and board.texture_drop is None
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "drop-into-frame.png"))
        release()
    elif step == 4:
        assert board.organization.membership["c"] == right and board.group_drop is None
        drag(center("c"), (780, 710))
    elif step == 5:
        assert board.group_drop["target"] == ""
        release()
    elif step == 6:
        assert "c" not in board.organization.membership
        positions = deepcopy(board.positions)
        drag(center("c"), header(left))
    elif step == 7:
        assert board.group_drop["target"] == left
        key("ESC")
    elif step == 8:
        assert board.positions == positions and "c" not in board.organization.membership
        assert board.card_drag is None and board.group_drop is None
        drag(center("c"), center("a"))
    elif step == 9:
        assert board.texture_drop is not None and board.group_drop is None
        key("ESC")
    elif step == 10:
        assert board.positions == positions and len(board.catalog.assets) == 3
        drag(header(left), header(right))
    elif step == 11:
        assert board.group_drop["target"] == right
        release()
    elif step == 12:
        assert board.organization.groups[left].parent == right
        positions = deepcopy(board.positions)
        drag(header(right), header(left))
    elif step == 13:
        assert board.group_drop is None, "Parent was allowed inside its own child"
        key("ESC")
    elif step == 14:
        assert board.positions == positions and board.organization.groups[left].parent == right
        drag(center("c"), header(right), ctrl=True)
    elif step == 15:
        assert board.group_drop is None and board.texture_drop is None
        release()  # Releasing Ctrl first must still suppress reparenting/baking.
    elif step == 16:
        assert "c" not in board.organization.membership and board.positions["c"] != positions["c"]
        board.positions["c"] = list(positions["c"])
        mouse("MOUSEMOVE", "NOTHING", (400, 500))
        key("N")
    elif step == 17:
        area = canvas.board_area(board)
        region = next(item for item in area.regions if item.type == "UI")
        if region.width < 50:
            return .2
        region.active_panel_category = "Remi"
    elif step == 18:
        before_menu = deepcopy(board.positions)
        move_button(4)  # Root C, after Group B / Group A / a / b.
    elif step == 19:
        assert board.selected == {"c"} and not board.group_selected
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "move-layer-menu.png"))
        key("HOME")
        key("DOWN_ARROW")
        key("DOWN_ARROW")  # Group B / Group A.
        key("RET")
    elif step == 20:
        assert board.organization.membership["c"] == left
        assert board.positions == before_menu
        move_button(1)  # Move nested Group A out to root, retaining a and C.
    elif step == 21:
        key("HOME")
        key("RET")
    elif step == 22:
        assert board.organization.groups[left].parent == ""
        assert board.organization.members(left) == {"a", "c"}
        assert board.positions == before_menu
        canvas.save_layout(bpy.context)
        saved = json.loads(scene[canvas.SCENE_ASSETS_KEY])
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "group-moves.blend"))
        bpy.ops.wm.open_mainfile(filepath=str(OUTPUT / "group-moves.blend"))
    elif step == 23:
        window, scene = bpy.context.window, bpy.context.scene
        if board is None:
            return .2
        assert board.organization.documents() == saved["organization"]["groups"]
        assert board.organization.membership == saved["organization"]["membership"]
        assert set(scene.objects.keys()) == objects
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "reopened-group-moves.png"))
        if installed:
            assert bpy.ops.preferences.addon_disable(module=package) == {"FINISHED"}
        else:
            remi.unregister()
        print(f"REMI_BOARD_GROUP_DROP_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
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
        print(f"REMI_BOARD_GROUP_DROP_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=.6, persistent=True)
