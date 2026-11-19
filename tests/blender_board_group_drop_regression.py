"""Stable board drop zones, hierarchy moves, selection targeting and cancellation."""

from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, organizing
from remi.features.board.assets import Asset


def center(board, rect):
    x, y, w, h = organizing.screen_rect(board, rect)
    return x+w/2, y+h/2


try:
    with tempfile.TemporaryDirectory(prefix="remi-board-group-drop-") as temporary:
        os.environ["REMI_BOARD_LAYOUT"] = str(Path(temporary) / "layout.json")
        area = bpy.context.screen.areas[0]
        with bpy.context.temp_override(area=area):
            board = canvas.Board(bpy.context)
        canvas.BOARD = board
        board.region, board.zoom, board.pan = (1200, 900), .6, [120, 300]
        for index, key in enumerate(("a", "b", "c")):
            board.catalog.assets[key] = Asset(key, "MESH", title=key)
            board.positions[key] = [index*500, 0]
            board.order.append(key)
        left = board.organization.create({"a"}, title="Left")
        right = board.organization.create({"b"}, title="Right")
        documents = deepcopy(board.catalog.documents())
        board.selected = {"c"}
        snapshot = organizing.drag_snapshot(board, {"c"})
        snapshot["moved"] = True
        point = center(board, snapshot["bounds"][right.id])
        drop = organizing.drop_target(board, snapshot, *point)
        assert drop["target"] == right.id
        assert organizing.apply_drop(board, snapshot, drop)
        assert board.organization.membership["c"] == right.id

        # Moving a member outside its original frame does not make its own
        # continuously growing auto-fit frame swallow every possible drop.
        snapshot = organizing.drag_snapshot(board, {"c"})
        snapshot["moved"] = True
        board.positions["c"] = [1600, 600]
        drop = organizing.drop_target(board, snapshot, 1100, 780)
        assert drop["target"] == ""
        organizing.apply_drop(board, snapshot, drop)
        assert "c" not in board.organization.membership
        snapshot = organizing.drag_snapshot(board, {"a", "c"})
        snapshot["moved"] = True
        positions, anchors = deepcopy(board.positions), deepcopy(snapshot["anchors"])
        board.positions["a"], board.positions["c"] = [500, 500], [900, 500]
        assert organizing.drop_target(board, snapshot, -30, 300) is None
        assert organizing.drop_target(board, snapshot, *center(board, snapshot["bounds"][right.id]))["target"] == right.id
        snapshot["move_only"] = True
        assert organizing.drop_target(board, snapshot, *point) is None
        organizing.cancel_drag(board, snapshot)
        assert board.positions == positions
        assert {key: group.anchor for key, group in board.organization.groups.items()} == anchors
        assert board.organization.membership["a"] == left.id

        # Groups move as subtrees, and own descendants are never drop targets.
        snapshot = organizing.drag_snapshot(board, {"a"}, {left.id})
        snapshot["moved"] = True
        drop = organizing.drop_target(board, snapshot, *point)
        organizing.apply_drop(board, snapshot, drop)
        assert left.parent == right.id and board.organization.membership["a"] == left.id
        snapshot = organizing.drag_snapshot(board, board.organization.members(right.id), {right.id})
        snapshot["moved"] = True
        assert organizing.drop_target(board, snapshot, *center(board, snapshot["bounds"][left.id])) is None
        snapshot = organizing.drag_snapshot(board, {"c"})
        snapshot["moved"] = True
        assert organizing.drop_target(board, snapshot, *center(board, snapshot["bounds"][left.id]))["target"] == left.id
        snapshot = organizing.drag_snapshot(board, {"a"}, {left.id})
        snapshot["moved"] = True
        drop = organizing.drop_target(board, snapshot, 1100, 780)
        assert drop["target"] == ""
        organizing.apply_drop(board, snapshot, drop)
        assert left.parent == "" and board.organization.membership["a"] == left.id

        # The per-layer move button targets a child row, not the selected parent.
        organizing.select_group(board, right.id)
        organizing.select_entry(board, "a")
        assert board.selected == {"a"} and not board.group_selected
        board.selected = {"a", "c"}
        organizing.select_entry(board, "c")
        assert board.selected == {"a", "c"}
        organizing.select_entry(board, left.id)
        assert board.group_selected == {left.id} and board.selected == {"a"}
        assert board.catalog.documents() == documents
        print("REMI_BOARD_GROUP_DROP_REGRESSION_OK", flush=True)
finally:
    canvas.stop_board(save=False)
    remi.unregister()
