"""N-panel scope, thumbnail fallback, framing, selection and saved fold state."""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, layers
from remi.features.board.assets import Asset
from remi.features.board.organization import group_bounds, layer_rows


def new_board():
    area = bpy.context.screen.areas[0]
    area.type = "IMAGE_EDITOR"
    area.spaces.active.show_region_ui = False
    with bpy.context.temp_override(area=area):
        result = canvas.Board(bpy.context)
    canvas.BOARD = result
    result.region = (1000, 850)
    return result, area


try:
    with tempfile.TemporaryDirectory(prefix="remi-board-layers-") as temporary:
        output = Path(temporary)
        os.environ["REMI_BOARD_LAYOUT"] = str(output / "layout.json")
        scene = bpy.context.scene
        scene.remi_image.output = str(output / "images")
        scene.remi_generation.output = str(output / "models")
        original_objects = set(scene.objects.keys())
        image = bpy.data.images.new("Layer thumbnail", 16, 16)
        image.filepath_raw, image.file_format = str(output / "reference.png"), "PNG"
        image.save()
        bpy.data.images.remove(image)
        board, area = new_board()
        a = Asset("a", "IMAGE", artifact=str(output / "reference.png"), title="Original prompt", label="Ocean")
        b = Asset("b", "MESH", artifact=str(output / "never-load.glb"), preview=a.artifact, title="Hull")
        c = Asset("c", "SPLAT", artifact=str(output / "never-load.npz"), title="Queued source", state="QUEUED")
        for index, asset in enumerate((a, b, c)):
            board.catalog.assets[asset.id] = asset
            board.positions[asset.id] = [20000+index*600, -12000]
            board.order.append(asset.id)
        child = board.organization.create({"a", "b"}, title="Parts")
        parent = board.organization.create({"c"}, {child.id}, "Ship")
        positions = deepcopy(board.positions)
        documents = deepcopy(board.catalog.documents())
        layers.thumbnail_icon(a)
        layers.thumbnail_icon(b)
        assert layers._previews is not None and a.artifact in layers._previews
        assert layers._icons[a.artifact][1] is not None  # Headless Blender has no UI icon IDs.
        assert layers.thumbnail_icon(c) == 0
        missing = Asset("missing", "IMAGE", artifact=str(output / "missing.png"))
        assert layers.thumbnail_icon(missing) == 0
        for identifier in ("a", child.id, parent.id):
            board.zoom, board.pan = .12, [-9000, 6000]
            layers.reveal(board, identifier)
            rect = group_bounds(board)[identifier] if identifier.startswith("group:") else (
                *board.positions[identifier], 300, 300)
            x, y, w, h = rect
            x, y, w, h = x*board.zoom+board.pan[0], y*board.zoom+board.pan[1], w*board.zoom, h*board.zoom
            assert 75 <= x and x+w <= board.region[0]-20, (identifier, x, w)
            assert 145 <= y and y+h <= board.region[1]-20, (identifier, y, h)
        assert board.group_selected == {parent.id} and board.selected == {"a", "b", "c"}
        layers.reveal(board, "a")
        assert board.selected == {"a"} and not board.group_selected
        layers.reveal(board, "b", extend=True)
        assert board.selected == {"a", "b"}
        assert board.positions == positions and board.catalog.documents() == documents
        assert set(scene.objects.keys()) == original_objects
        assert board.runner.job is None and board.runner.preview_job is None
        with bpy.context.temp_override(area=area):
            assert layers.REMI_BOARD_PT_layers.poll(bpy.context)
            assert bpy.ops.remi_board.layer_fold(target=child.id) == {"FINISHED"}
            assert child.id in board.layers_collapsed
            assert bpy.ops.remi_board.reveal_layer(target="c") == {"FINISHED"}
            assert board.selected == {"c"} and not board.group_selected
        other = next(item for item in bpy.context.screen.areas if item != area)
        with bpy.context.temp_override(area=other):
            assert not layers.REMI_BOARD_PT_layers.poll(bpy.context)
        saved = json.loads(scene[canvas.SCENE_ASSETS_KEY])
        assert saved["layers_collapsed"] == [child.id]
        bpy.ops.wm.save_as_mainfile(filepath=str(output / "layers.blend"))
        bpy.ops.wm.open_mainfile(filepath=str(output / "layers.blend"))
        board, area = new_board()
        board.load(bpy.context, [])
        assert board.layers_collapsed == {child.id}
        assert board.organization.groups[child.id].parent == parent.id
        assert [row.id for row in layer_rows(board, query="Ocean", collapsed=board.layers_collapsed)] == [parent.id, child.id, "a"]
        # Native sidebar geometry: overlapping and non-overlapping layouts.
        window = SimpleNamespace(type="WINDOW", x=0, width=1000, height=850)
        ui = SimpleNamespace(type="UI", x=700, width=300, height=850)
        fake_area = SimpleNamespace(regions=[window, ui], spaces=SimpleNamespace(active=SimpleNamespace(show_region_ui=True)))
        assert canvas.board_view_size(fake_area, window) == (700, 850)
        window.width = 700
        assert canvas.board_view_size(fake_area, window) == (700, 850)
        fake_area.spaces.active.show_region_ui = False
        window.width = 1000
        assert canvas.board_view_size(fake_area, window) == (1000, 850)
        print("REMI_BOARD_LAYERS_REGRESSION_OK", flush=True)
finally:
    canvas.stop_board(save=False)
    remi.unregister()
