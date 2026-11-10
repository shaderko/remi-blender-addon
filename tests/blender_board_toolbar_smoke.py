"""Fixed toolbar, context gating, real settings popups and saved job recipes.

Run with --enable-event-simulate. Workers are paused: no model inference.
"""

from datetime import datetime
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest import mock

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_toolbar_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, toolbar
from remi.features.board.assets import Asset
from remi.integrations.triposplat.splat_data import save as save_splats


window, scene = bpy.context.window, bpy.context.scene
scene.remi_image.output = str(OUTPUT / "images")
scene.remi_generation.output = str(OUTPUT / "models")
scene.remi_generation.mesh_output = str(OUTPUT / "meshes")
original_objects = set(scene.objects.keys())
image = bpy.data.images.new("Toolbar reference", 64, 64)
image.generated_color = (0.16, 0.36, 0.6, 1)
image.filepath_raw = str(OUTPUT / "reference.png")
image.file_format = "PNG"
image.save()
bpy.data.images.remove(image)
ply = OUTPUT / "mesh.ply"
ply.write_text("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
               "element face 4\nproperty list uchar int vertex_indices\nend_header\n"
               "-1 -1 0\n1 -1 0\n0 1 0\n0 0 1\n3 0 2 1\n3 0 1 3\n3 1 2 3\n3 2 0 3\n")
archive = OUTPUT / "splats.npz"
save_splats(archive, {"positions": np.array([[0, 0, 0], [0.4, 0, 0], [0, 0.4, 0]], np.float32),
    "scales": np.full((3, 3), 0.15, np.float32),
    "rotations": np.tile(np.array([1, 0, 0, 0], np.float32), (3, 1)),
    "opacities": np.ones(3, np.float32), "sh_dc": np.zeros((3, 3), np.float32)})
hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (ply, archive)}
step = waits = 0
freeze = None
geometry = None
flow_id = None
pan_origin = None


def enabled():
    return {tool.id for tool in toolbar.TOOLS if toolbar.enabled(canvas.BOARD, tool)[0]}


def click_tool(key, button="LEFTMOUSE"):
    rect = next(rect for tool, rect in toolbar.buttons(canvas.BOARD) if tool.id == key)
    region = canvas.window_region(canvas.board_area(canvas.BOARD))
    x, y = int(region.x+rect[0]+rect[2]/2), int(region.y+rect[1]+rect[3]/2)
    window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
    window.event_simulate(type=button, value="PRESS", x=x, y=y)
    window.event_simulate(type=button, value="RELEASE", x=x, y=y)


def dismiss():
    window.event_simulate(type="ESC", value="PRESS")
    window.event_simulate(type="ESC", value="RELEASE")


def newest(operation):
    return next(asset for asset in reversed(list(canvas.BOARD.catalog.assets.values())) if asset.operation == operation)


def advance():
    global step, waits, freeze, geometry, flow_id, window, scene, pan_origin
    board = canvas.BOARD
    waits += 1
    assert waits < 150, f"Timed out at step {step}"
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        if board is None:
            return 0.2
        values = [Asset("image", "IMAGE", artifact=str(OUTPUT / "reference.png"), title="Reference"),
                  Asset("splat", "SPLAT", artifact=str(archive), parent="image", title="3D source"),
                  Asset("mesh", "MESH", artifact=str(ply), parent="splat", title="Mesh")]
        for index, asset in enumerate(values):
            board.catalog.assets[asset.id] = asset
            board.positions[asset.id] = [index*280.0, 100.0]
            board.scales[asset.id] = 0.7
            board.order.append(asset.id)
        board.zoom, board.pan = 0.85, [105.0, 340.0]
        board.composer = "Keep this prompt while using the toolbar"
        canvas.save_layout(bpy.context)
    elif step == 2:
        if any(board.texture(key) is None for key in ("image", "splat", "mesh")):
            assert not board.preview_errors, board.preview_errors
            return 0.2
        freeze = mock.patch.object(board.runner, "tick", return_value=False)
        freeze.start()
        geometry = [(tool.id, rect) for tool, rect in toolbar.buttons(board)]
        for selection, expected in ((set(), {"import"}),
                                    ({"image"}, {"import", "generate3d", "export"}),
                                    ({"splat"}, {"import", "mesh", "add3d", "export"}),
                                    ({"mesh"}, {"import", "flow", "texture", "editmesh", "add3d", "export"}),
                                    ({"image", "splat"}, {"import"})):
            board.selected = selection
            assert enabled() == expected, (selection, enabled())
            assert [(tool.id, rect) for tool, rect in toolbar.buttons(board)] == geometry
        board.selected = {"image"}
        click_tool("flow")  # Disabled click must not change selection or run anything.
    elif step == 3:
        assert board.selected == {"image"} and len(board.catalog.assets) == 3
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "image-selected.png"))
        board.focused = True
        click_tool("mesh", "RIGHTMOUSE")  # Settings work even while the action is disabled.
    elif step == 4:
        assert not board.focused and len(board.catalog.assets) == 3
        scene.remi_generation.mesh_quality = "FAST"
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "mesh-settings.png"))
        dismiss()
    elif step == 5:
        board.selected = {"splat"}
        click_tool("mesh")
    elif step == 6:
        result = newest("CREATE_MESH")
        assert result.state == "QUEUED" and result.settings["quality"] == "FAST"
        assert board.selected == set(canvas.selected_paths(bpy.context)) == {result.id}
        assert len(board.catalog.assets) == 4, "Left-click waited for a second confirmation"
        click_tool("generate3d", "RIGHTMOUSE")
    elif step == 7:
        assert len(board.catalog.assets) == 4, "Right-click started a job"
        scene.remi_generation.seed = 314159
        scene.remi_generation.gaussians = "32768"
        dismiss()
    elif step == 8:
        board.selected = {"image"}
        click_tool("generate3d")
    elif step == 9:
        result = newest("GENERATE_3D")
        assert result.settings["seed"] == 314159 and result.settings["gaussians"] == 32768
        assert board.selected == set(canvas.selected_paths(bpy.context)) == {result.id}
        assert len(board.catalog.assets) == 5
        board.selected = {"mesh"}
        click_tool("flow", "RIGHTMOUSE")
    elif step == 10:
        assert len(board.catalog.assets) == 5
        from remi.app.flow import initialize_steps
        initialize_steps(scene.remi_flow, ("UV",))
        scene.remi_flow.preset = "CURRENT"
        scene.remi_settings.bake_texture_size = 512
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "flow-settings.png"))
        dismiss()
    elif step == 11:
        board.selected = {"mesh"}
        click_tool("flow")
    elif step == 12:
        result = newest("FLOW")
        flow_id = result.id
        assert result.settings["flow"]["actions"] == ["UV"]
        assert result.settings["flow"]["settings"]["bake_texture_size"] == 512
        assert board.selected == set(canvas.selected_paths(bpy.context)) == {result.id}
        assert enabled() == {"import", "cancel"}
        click_tool("cancel")
    elif step == 13:
        assert board.catalog.assets[flow_id].state == "CANCELLED"
        board.selected = {"mesh"}
        click_tool("texture", "RIGHTMOUSE")
    elif step == 14:
        assert len(board.catalog.assets) == 6
        scene.remi_texture.texture_size = "1024"
        scene.remi_texture.normal_smoothing = 4
        dismiss()
    elif step == 15:
        click_tool("texture")
    elif step == 16:
        result = newest("TEXTURE")
        assert result.links["texture_source"] == "splat", "Toolbar did not resolve the mesh's splat lineage"
        assert result.settings["texture"]["texture_size"] == 1024
        assert result.settings["texture"]["normal_smoothing"] == 4
        assert board.selected == set(canvas.selected_paths(bpy.context)) == {result.id}
        assert not board.catalog.assets["mesh"].links
        assert board.composer == "Keep this prompt while using the toolbar"
        assert not hasattr(board, "button3d_rect")
        board.selected = {"mesh"}
        canvas.draw_selection_strip(board, board.region[0])
        assert {action for _rect, action, _target in board.strip_hits} == {"select"}
        for key in list(board.catalog.assets):
            if key not in {"image", "splat", "mesh"}:
                asset = board.catalog.assets[key]
                asset.state, asset.hidden = "CANCELLED", True
                board.forget(key)
        board.texture_effects.clear()
        board.zoom, board.pan = 0.85, [105.0, 340.0]
        freeze.stop()
        freeze = None
    elif step == 17:
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "mesh-selected.png"))
        # Middle-drag starts on an icon, crosses the rail, and never runs it.
        pan_origin = list(board.pan)
        rect = toolbar.buttons(board)[0][1]
        region = canvas.window_region(canvas.board_area(board))
        x, y = int(region.x+rect[0]+rect[2]/2), int(region.y+rect[1]+rect[3]/2)
        window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
        window.event_simulate(type="MIDDLEMOUSE", value="PRESS", x=x, y=y)
        window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x+23, y=y-17)
        window.event_simulate(type="MIDDLEMOUSE", value="RELEASE", x=x+23, y=y-17)
    elif step == 18:
        assert board.pan == [pan_origin[0]+23, pan_origin[1]-17], (board.pan, pan_origin)
        assert board.selected == {"mesh"} and len(board.catalog.assets) == 7
        assert board.pan_drag is None and not board.toolbar_pressed
        assert [(tool.id, rect) for tool, rect in toolbar.buttons(board)] == geometry
        assert set(scene.objects.keys()) == original_objects
        for path, digest in hashes.items():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "toolbar.blend"))
        bpy.ops.wm.open_mainfile(filepath=str(OUTPUT / "toolbar.blend"))
    elif step == 19:
        window, scene = bpy.context.window, bpy.context.scene
        if board is None:
            return 0.2
        assert scene.remi_generation.mesh_quality == "FAST" and scene.remi_generation.seed == 314159
        assert scene.remi_flow.preset == "CURRENT" and scene.remi_settings.bake_texture_size == 512
        assert scene.remi_texture.texture_size == "1024" and scene.remi_texture.normal_smoothing == 4
        assert [(tool.id, rect) for tool, rect in toolbar.buttons(board)] == geometry
        assert sum(op.bl_rna.identifier == "REMI_BOARD_OT_run" for op in window.modal_operators) == 1
        remi.unregister()
        print(f"REMI_BOARD_TOOLBAR_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return 0.4


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        print(f"REMI_BOARD_TOOLBAR_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=0.6, persistent=True)
