"""Persistent thumbnail proxy and progressive texture LOD; no GPU upload."""

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import types
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = module
    spec.loader.exec_module(module)

import remi
from remi.features.board import canvas
from remi.features.board.assets import Asset, Catalog

remi.register()

temporary = Path(tempfile.mkdtemp(prefix="remi-texture-lod-"))
source = temporary / "source.png"
image = bpy.data.images.new("Remi LOD source", 300, 300, alpha=True)
image.filepath_raw = str(source)
image.file_format = "PNG"
image.save()
bpy.data.images.remove(image)

board = types.SimpleNamespace(
    catalog=Catalog(),
    positions={"lod": [0.0, 0.0]},
    scales={},
    order=["lod"],
    selected=set(),
    textures={},
    texture_image_order=canvas.OrderedDict(),
    preview_errors={},
    loaded_previews={},
    dirty_previews=set(),
    thumbnail_checks={},
    orbit_drag=None,
    zoom=2.0,
    region=(1440, 810),
    card_rect=lambda path: (0.0, 0.0, 300.0, 300.0),
    navigating=lambda: False,
)
for name in ("texture", "load_texture", "trim_texture", "release_texture",
             "texture_target", "prepare_preview", "thumbnail_ready", "cleanup",
             "_load_texture_source", "_touch_texture_image"):
    setattr(board, name, types.MethodType(getattr(canvas.Board, name), board))
board._drop_texture_image = canvas.Board._drop_texture_image
board._next_texture_size = canvas.Board._next_texture_size
board.catalog.assets["lod"] = Asset("lod", "IMAGE", artifact=str(source), state="READY")

with mock.patch.object(canvas.gpu.texture, "from_image", return_value=object()):
    assert board.texture_target("lod") == canvas.THUMB
    board.zoom = 0.5
    assert board.texture_target("lod") == canvas.TEXTURE_MEDIUM
    board.zoom = 0.2
    assert board.texture_target("lod") == canvas.TEXTURE_PROXY
    board.card_rect = lambda path: (-400.0, 0.0, 300.0, 300.0)
    assert board.texture_target("lod") == canvas.TEXTURE_PROXY
    board.card_rect = lambda path: (0.0, 0.0, 300.0, 300.0)
    board.zoom = 2.0

    assert board.load_texture("lod", canvas.TEXTURE_PROXY) is True
    assert board.texture("lod") is not None
    state = board.textures["lod"]
    assert state["best"] == canvas.TEXTURE_PROXY
    assert state["image"] is not None

    # One call fills every missing tier up to the requested target.
    assert board.load_texture("lod", canvas.THUMB) is True
    assert state["best"] == 300
    assert set(state["tiers"]) == {canvas.TEXTURE_PROXY, canvas.TEXTURE_MEDIUM, 300}

    # Explicit trim without keep_image drops the decoded source but keeps the proxy.
    assert board.trim_texture("lod", canvas.TEXTURE_PROXY) is True
    assert state["best"] == canvas.TEXTURE_PROXY
    assert state["image"] is None
    assert board.texture("lod") is not None

    # Returning at full size reloads once and fills the target in a single call.
    assert board.load_texture("lod", canvas.THUMB) is True
    assert state["best"] == 300

    # Visible zoomed-out cards retain the decoded source, so zooming back in is instant.
    board.zoom = 0.2
    assert board.prepare_preview() is True
    assert state["best"] == canvas.TEXTURE_PROXY
    assert state["image"] is not None
    board.zoom = 2.0
    assert board.prepare_preview() is True
    assert state["best"] == 300
    assert board.texture("lod") is not None
    assert board.prepare_preview() is False

    # Off-screen cards still release the decoded source and keep the proxy.
    board.card_rect = lambda path: (-400.0, 0.0, 300.0, 300.0)
    assert board.prepare_preview() is True
    assert state["best"] == canvas.TEXTURE_PROXY
    assert state["image"] is None
    assert board.texture("lod") is not None

board.release_texture("lod")
assert "lod" not in board.textures
canvas.BOARD = None
remi.unregister()
shutil.rmtree(temporary, ignore_errors=True)
print("REMI_BOARD_TEXTURE_LOD_REGRESSION_OK", flush=True)

