"""Duplicate board cards as independent, shared-file-safe assets."""

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile

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

temporary = Path(tempfile.mkdtemp(prefix="remi-duplicate-"))
artifact = temporary / "asset.glb"
artifact.write_bytes(b"mesh")
native = temporary / "asset.blend"
native.write_bytes(b"native")
preview = temporary / "preview.png"
preview.write_bytes(b"preview")


class FakeBoard:
    scene = bpy.context.scene
    catalog = Catalog()
    selected = set()
    positions = {}
    scales = {}
    order = []
    pan = [0.0, 0.0]
    region = (1440, 810)

    def place_asset(self, identifier, parent=""):
        self.positions[identifier] = [len(self.positions) * 10.0, 0.0]
        self.scales[identifier] = 1.0

    def card_rect(self, path):
        return (0.0, 0.0, 300.0, 300.0)


board = FakeBoard()
canvas.BOARD = board
canvas.store_selection = lambda context, paths: None
canvas.save_layout = lambda context: None
canvas.board_area = lambda board: None

source = Asset(
    "asset:source", "MESH", artifact=str(artifact), native_artifact=str(native),
    parent="splat:1", title="Ship", operation="FLOW", state="READY", status="Ready",
    progress=1.0, preview=str(preview), preview_view=[12.0, 34.0],
    directory=str(temporary), settings={"flow": {"name": "Clean"}},
    yaw=12.0, elevation=34.0, links={"texture_source": "splat:1"},
    revisions=[{"artifact": str(artifact), "native_artifact": str(native),
                "directory": str(temporary), "preview": str(preview)}],
)
board.catalog.assets[source.id] = source
board.positions[source.id] = [0.0, 0.0]

created = canvas.duplicate_assets(bpy.context, ["asset:source"])
assert len(created) == 1
copy = created[0]
assert copy.id != source.id and copy.title == "Ship Copy"
assert (copy.kind, copy.state, copy.parent, copy.operation) == (
    source.kind, "READY", source.parent, source.operation)
assert copy.artifact == source.artifact and copy.native_artifact == source.native_artifact
assert copy.preview == source.preview and copy.preview_view == source.preview_view
assert copy.settings == source.settings and copy.settings is not source.settings
assert copy.links == source.links and copy.links is not source.links
assert copy.revisions == source.revisions and copy.revisions is not source.revisions
assert copy.yaw == 12.0 and copy.elevation == 34.0
assert board.selected == {copy.id}
assert board.positions[copy.id] != board.positions[source.id]
assert Catalog([copy.document()]).assets[copy.id].title == "Ship Copy"

second = canvas.duplicate_assets(bpy.context, ["asset:source"])[0]
assert second.title == "Ship Copy 2" and second.id not in {source.id, copy.id}

# Duplicating a group selects all copies and keeps their titles unique.
board.selected = {"asset:source", copy.id}
group = canvas.duplicate_assets(bpy.context, ["asset:source", copy.id])
assert len(group) == 2 and board.selected == {asset.id for asset in group}
assert len({asset.title for asset in board.catalog.assets.values()}) == len(board.catalog.assets)
assert {item.title for item in group} == {"Ship Copy 3", "Ship Copy Copy"}

# Shared files stay shared; deleting either card keeps the other usable.
assert set(copy.files()) & set(source.files())

canvas.BOARD = None
remi.unregister()
shutil.rmtree(temporary, ignore_errors=True)
print("REMI_BOARD_DUPLICATE_REGRESSION_OK", flush=True)
