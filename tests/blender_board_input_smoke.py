"""Real mouse regression: marquee, group movement, and MMB pan anywhere."""

from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sys
import shutil
import tempfile
import traceback
from unittest import mock

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_board_input_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
from remi.features.board import canvas, toolbar
from remi.features.board.assets import Asset

image = bpy.data.images.new("Mouse fixture", 64, 64)
image.generated_color = (0.12, 0.35, 0.65, 1)
image.filepath_raw = str(OUTPUT / "card.png")
image.file_format = "PNG"
image.save()
bpy.data.images.remove(image)
bpy.context.scene.remi_image.output = str(OUTPUT / "images")
bpy.context.scene.remi_generation.output = str(OUTPUT / "models")
objects = set(bpy.context.scene.objects.keys())
step = 0
pairing = None


def mouse(kind, value, x, y, **modifiers):
    region = canvas.window_region(canvas.board_area(canvas.BOARD))
    bpy.context.window.event_simulate(type=kind, value=value, x=int(region.x+x), y=int(region.y+y), **modifiers)


def click(x, y, **modifiers):
    mouse("MOUSEMOVE", "NOTHING", x, y, **modifiers)
    mouse("LEFTMOUSE", "PRESS", x, y, **modifiers)
    mouse("LEFTMOUSE", "RELEASE", x, y, **modifiers)


def centre(identifier):
    x, y, w, h = canvas.BOARD.card_rect(identifier)
    return x+w/2, y+h/2


def advance():
    global step, pairing
    try:
        board = canvas.BOARD
        if step == 0:
            bpy.ops.remi_board.open()
        elif step == 1:
            if board is None:
                return 0.1
            for index, identifier in enumerate(("a", "b", "c")):
                board.catalog.assets[identifier] = Asset(identifier, "IMAGE", artifact=str(OUTPUT / "card.png"))
                board.order.append(identifier)
                board.positions[identifier] = [index*200.0, 0.0]
                board.scales[identifier] = 0.5
            board.zoom, board.pan = 1.0, [60.0, 350.0]
            board.selected = {"c"}
            canvas.board_area(board).tag_redraw()
        elif step == 2:
            mouse("MOUSEMOVE", "NOTHING", 40, 330)
            mouse("LEFTMOUSE", "PRESS", 40, 330)
            mouse("MOUSEMOVE", "NOTHING", 430, 530)
        elif step == 3:
            assert board.box_drag is not None and board.selected == {"a", "b"}, board.selected
            assert board.pan == [60, 350], "Left marquee panned the board"
            assert board.positions["a"] == [0, 0] and board.positions["b"] == [200, 0]
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / "selection.png"))
            mouse("LEFTMOUSE", "RELEASE", 430, 530)
        elif step == 4:
            assert board.box_drag is None
            x, y = centre("a")
            mouse("MOUSEMOVE", "NOTHING", x, y)
            mouse("LEFTMOUSE", "PRESS", x, y)
            mouse("MOUSEMOVE", "NOTHING", x+45, y+30)
        elif step == 5:
            assert board.selected == {"a", "b"}
            assert board.positions["a"] == [45, 30] and board.positions["b"] == [245, 30], board.positions
            assert board.positions["c"] == [400, 0]
            mouse("LEFTMOUSE", "RELEASE", *centre("a"))
        elif step == 6:
            x, y = centre("c")
            mouse("MOUSEMOVE", "NOTHING", x, y)
            mouse("MIDDLEMOUSE", "PRESS", x, y)
            mouse("MOUSEMOVE", "NOTHING", x+80, y-40)
            mouse("MIDDLEMOUSE", "RELEASE", x+80, y-40)
        elif step == 7:
            assert board.pan == [140, 310], board.pan
            assert board.positions["c"] == [400, 0], "Middle-drag on a card moved the card"
            assert board.selected == {"a", "b"} and board.pan_drag is None
            click(20, 250)
        elif step == 8:
            assert not board.selected
            click(*centre("a"))
            click(*centre("c"), shift=True)
        elif step == 9:
            assert board.selected == {"a", "c"}, board.selected
            x, y, w, h = board.card_rect("c")
            mouse("MOUSEMOVE", "NOTHING", x-4, y-4, ctrl=True)
            mouse("LEFTMOUSE", "PRESS", x-4, y-4, ctrl=True)
            mouse("MOUSEMOVE", "NOTHING", x+w+4, y+h+4, ctrl=True)
            mouse("LEFTMOUSE", "RELEASE", x+w+4, y+h+4, ctrl=True)
        elif step == 10:
            assert board.selected == {"a"}, board.selected
            x, y = centre("a")
            mouse("MOUSEMOVE", "NOTHING", x, y)
            mouse("LEFTMOUSE", "PRESS", x, y)
            mouse("MOUSEMOVE", "NOTHING", x+1, y+1)
            mouse("LEFTMOUSE", "RELEASE", x+1, y+1)
        elif step == 11:
            assert board.positions["a"] == [45, 30], "Click jitter moved a card"
            with mock.patch.object(canvas, "text_width", wraps=canvas.text_width) as measured:
                clipped = canvas.clip_text("A detailed bowl "*400, 12, 100)
                assert clipped.endswith("…") and measured.call_count <= 16
            board.pan_drag = (0, 0)
            with mock.patch.object(board, "load_texture", side_effect=AssertionError("Decoded an image while navigating")), \
                 mock.patch.object(canvas, "Preview", side_effect=AssertionError("Rendered a thumbnail while navigating")):
                assert not board.prepare_preview()
            board.pan_drag = None
            region = canvas.window_region(canvas.board_area(board))
            mouse("MIDDLEMOUSE", "PRESS", 30, 250)
            mouse("MOUSEMOVE", "NOTHING", region.width+20, 250)
            mouse("MIDDLEMOUSE", "RELEASE", region.width+20, 250)
        elif step == 12:
            assert board.pan_drag is None, "Releasing outside the editor left a stuck drag"
            assert board.selected == {"a"}
            assert set(bpy.context.scene.objects.keys()) == objects
            shutil.copy2(OUTPUT / "card.png", OUTPUT / "second.png")
            area = canvas.board_area(board)
            with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
                assert canvas.IMAGE_FH_remi_board.poll_drop(bpy.context)
                assert not bpy.types.IMAGE_FH_drop_handler.poll_drop(bpy.context)
                assert bpy.ops.remi_board.drop_images(directory=str(OUTPUT),
                    files=[{"name": "card.png"}, {"name": "second.png"}],
                    at_cursor=True, drop_x=300, drop_y=400) == {"FINISHED"}
                imported = set(board.selected)
                assert len(imported) == 2
                copies = [Path(board.catalog.assets[key].artifact) for key in imported]
                assert all(path.is_file() for path in copies)
                revision_file = OUTPUT / "revision.png"
                shutil.copy2(OUTPUT / "card.png", revision_file)
                board.catalog.assets[next(iter(imported))].revisions = [{"artifact": str(revision_file)}]
                # A backup referenced by an unselected card must also survive.
                shared = OUTPUT / "shared-revision.png"
                shutil.copy2(OUTPUT / "card.png", shared)
                board.catalog.assets[next(iter(imported))].revisions.append({"artifact": str(shared)})
                board.catalog.assets["a"].revisions = [{"artifact": str(shared)}]
                assert bpy.ops.remi_board.delete() == {"FINISHED"}
                assert all(key not in board.order for key in imported)
                assert all(not path.exists() for path in copies)
                assert not revision_file.exists() and shared.exists()
                assert (OUTPUT / "card.png").is_file() and (OUTPUT / "second.png").is_file()
            target = Asset("texture-target", "MESH", title="Mesh", state="READY",
                            artifact=str(OUTPUT / "mesh.glb"))
            source = Asset("texture-source", "SPLAT", state="READY",
                           artifact=str(OUTPUT / "splats.npz"))
            board.catalog.assets[target.id] = target
            board.catalog.assets[source.id] = source
            board.order += [target.id, source.id]
            board.zoom, board.pan = 0.5, [0.0, 0.0]
            board.positions[target.id] = [50.0, 200.0]
            board.positions[source.id] = [300.0, 200.0]
            pairing = {"target": target.id, "source": source.id,
                       "mesh": centre(target.id), "handle": board.output_handle(source.id)}
            mouse("MOUSEMOVE", "NOTHING", *pairing["handle"])
            mouse("LEFTMOUSE", "PRESS", *pairing["handle"])
        elif step == 13:
            assert board.link_drag is not None, "3D output handle did not start a link drag"
            mouse("MOUSEMOVE", "NOTHING", *pairing["mesh"])
            mouse("LEFTMOUSE", "RELEASE", *pairing["mesh"])
        elif step == 14:
            target = board.catalog.assets[pairing["target"]]
            source = board.catalog.assets[pairing["source"]]
            assert target.links["texture_source"] == source.id, "Graph drag did not link appearance"
            assert board.selected == {target.id}
            assert board.link_drag is None
            canvas.draw_selection_strip(board, board.region[0])
            actions = {action for _rect, action, _target in board.strip_hits}
            assert actions == {"select"}, actions
            assert {"editmesh", "add3d"} <= {tool.id for tool in toolbar.TOOLS if toolbar.enabled(board, tool)[0]}
            board.selected = {source.id}
            canvas.draw_selection_strip(board, board.region[0])
            assert toolbar.enabled(board, toolbar.BY_ID["add3d"])[0]
            assert bpy.ops.remi_board.unlink_texture(asset_id=target.id) == {"FINISHED"}
            assert "texture_source" not in target.links
            remi.unregister()
            assert canvas.before_save not in bpy.app.handlers.save_pre
            print(f"REMI_BOARD_INPUT_SMOKE_OK: {OUTPUT}", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 0.15
    except Exception:
        traceback.print_exc()
        print(f"REMI_BOARD_INPUT_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(advance, first_interval=0.5)
