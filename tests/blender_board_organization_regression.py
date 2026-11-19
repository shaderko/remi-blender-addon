"""Board names, nested padded frames, safe removal, drag/cancel and persistence."""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, organizing
from remi.features.board.assets import Asset
from remi.features.board.organization import group_bounds


def new_board():
    area = bpy.context.screen.areas[0]
    with bpy.context.temp_override(area=area):
        value = canvas.Board(bpy.context)
    canvas.BOARD = value
    return value


def event(kind, value="PRESS", **kwargs):
    return SimpleNamespace(type=kind, value=value, ctrl=False, oskey=False, shift=False, alt=False, **kwargs)


def header(board, identifier):
    x, y, w, h = organizing.screen_rect(board, group_bounds(board)[identifier])
    return x+w/2, y+h-max(18, 38*board.zoom)/2


class MenuLayout:
    operator_context = "EXEC_REGION_WIN"

    def __init__(self):
        self.calls = []

    def operator(self, identifier, **kwargs):
        self.calls.append((identifier, self.operator_context))
        return SimpleNamespace()

    def menu(self, identifier):
        self.calls.append((identifier, self.operator_context))


try:
    with tempfile.TemporaryDirectory(prefix="remi-organization-") as temporary:
        output = Path(temporary)
        os.environ["REMI_BOARD_LAYOUT"] = str(output / "layout.json")
        scene = bpy.context.scene
        scene.remi_image.output = str(output / "images")
        scene.remi_generation.output = str(output / "models")
        artifact = output / "untouched.glb"
        artifact.write_bytes(b"keep all asset files")
        board = new_board()
        board.region = (1440, 900)
        board.zoom, board.pan = .8, [120, 250]
        for index, key in enumerate(("a", "b", "c")):
            board.catalog.assets[key] = Asset(key, "MESH", artifact=str(artifact), title="Mesh " + key)
            board.positions[key] = [index*380, 0]
            board.order.append(key)
        image = Asset("image", "IMAGE", artifact=str(output / "image.png"), title="Original image generation prompt")
        board.catalog.assets[image.id] = image
        original = {key: (asset.title, asset.artifact, asset.parent) for key, asset in board.catalog.assets.items()}
        layout = MenuLayout()
        organizing.draw_actions(layout, target="image")
        assert layout.calls == [("remi_board.rename", "INVOKE_REGION_WIN"),
                                ("remi_board.group", "INVOKE_REGION_WIN"),
                                ("REMI_BOARD_MT_move_to_group", "EXEC_REGION_WIN"),
                                ("remi_board.ungroup", "EXEC_REGION_WIN")]
        assert layout.operator_context == "EXEC_REGION_WIN"
        assert bpy.ops.remi_board.rename(target="image", name="  Reference\n  picture ") == {"FINISHED"}
        assert image.label == "Reference picture"
        assert canvas.prompt_for(board, "image") == "Original image generation prompt"
        board.selected = {"a", "b"}
        assert bpy.ops.remi_board.group(name="Parts") == {"FINISHED"}
        child = next(iter(board.group_selected))
        board.selected.add("c")
        assert bpy.ops.remi_board.group(name="Assembly") == {"FINISHED"}
        parent = next(iter(board.group_selected))
        assert board.organization.groups[child].parent == parent
        assert board.organization.members(parent) == {"a", "b", "c"}
        assert bpy.ops.remi_board.rename(target=parent, name="Ship") == {"FINISHED"}
        assert board.organization.path(child) == "Ship / Parts"
        for key, asset in board.catalog.assets.items():
            assert (asset.title, asset.artifact, asset.parent) == original[key]

        before = deepcopy(board.positions)
        anchors = {key: list(group.anchor) for key, group in board.organization.groups.items()}
        x, y = header(board, parent)
        assert organizing.handle_event(bpy.context, board, event("LEFTMOUSE"), x, y)
        assert board.selected == {"a", "b", "c"} and board.group_drag
        assert organizing.handle_event(bpy.context, board, event("MOUSEMOVE", "NOTHING"), x+80, y+40)
        assert all(board.positions[key] == [pos[0]+100, pos[1]+50] for key, pos in before.items())
        assert organizing.handle_event(bpy.context, board, event("ESC"), x+80, y+40)
        assert board.positions == before and board.group_drag is None
        assert {key: group.anchor for key, group in board.organization.groups.items()} == anchors
        assert organizing.handle_event(bpy.context, board, event("LEFTMOUSE"), x, y)
        organizing.handle_event(bpy.context, board, event("MOUSEMOVE", "NOTHING"), x+80, y+40)
        organizing.handle_event(bpy.context, board, event("LEFTMOUSE", "RELEASE"), x+80, y+40)
        assert board.group_drag is None and board.selected == {"a", "b", "c"}
        assert all(board.positions[key] == [pos[0]+100, pos[1]+50] for key, pos in before.items())

        # Explicit card naming works in queued state too, without changing recipes.
        board.catalog.assets["a"].state = "QUEUED"
        assert bpy.ops.remi_board.rename(target="a", name="Hull") == {"FINISHED"}
        board.catalog.assets["a"].state = "READY"
        duplicate = canvas.duplicate_assets(bpy.context, {"a"})[0]
        assert duplicate.label == "Hull Copy" and duplicate.title == "Mesh a"
        assert board.organization.membership[duplicate.id] == child
        duplicate_image = canvas.duplicate_assets(bpy.context, {"image"})[0]
        assert duplicate_image.title == image.title and duplicate_image.label == "Reference picture Copy"
        assert canvas.prompt_for(board, duplicate_image.id) == image.title

        # Scene layout wins over a newer external layout and survives .blend load.
        canvas.save_layout(bpy.context)
        expected = json.loads(scene[canvas.SCENE_ASSETS_KEY])
        blend = output / "organization.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        scene = bpy.context.scene
        external = {"galleries": {canvas.layout_key(bpy.context): {"organization": {}, "positions": {}}}}
        (output / "layout.json").write_text(json.dumps(external))
        board = new_board()
        board.region = (1440, 900)
        board.load(bpy.context, [])
        assert board.organization.documents() == expected["organization"]["groups"]
        assert board.organization.membership == expected["organization"]["membership"]
        assert board.positions["a"] == expected["positions"]["a"]
        assert board.catalog.assets["a"].label == "Hull"
        assert board.catalog.assets["image"].title == image.title

        # Deleting a selected frame directly, as well as X/Del, must keep files.
        organizing.select_group(board, parent)
        positions = deepcopy(board.positions)
        assert bpy.ops.remi_board.delete() == {"FINISHED"}
        assert parent not in board.organization.groups and child in board.organization.groups
        assert board.organization.groups[child].parent == ""
        assert board.positions == positions and all(not asset.hidden for asset in board.catalog.assets.values())
        assert artifact.read_bytes() == b"keep all asset files"
        organizing.select_group(board, child)
        assert organizing.handle_event(bpy.context, board, event("DEL"), 300, 300)
        assert not board.organization.groups and not board.organization.membership
        assert artifact.is_file() and board.positions == positions
        print("REMI_BOARD_ORGANIZATION_REGRESSION_OK", flush=True)
finally:
    canvas.stop_board(save=False)
    remi.unregister()
