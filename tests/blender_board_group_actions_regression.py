"""Multi-card toolbar actions queue one independent job per selected card."""

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
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
from remi.features.board import canvas, toolbar
from remi.features.board.assets import Asset, Catalog

remi.register()

temporary = Path(tempfile.mkdtemp(prefix="remi-group-actions-"))
dummy = temporary / "source.bin"
dummy.write_bytes(b"source")
output = temporary / "board"
output.mkdir()


class FakeBoard:
    scene = bpy.context.scene
    catalog = Catalog()
    selected = set()
    texture_effects = {}
    positions = {}
    scales = {}
    order = []
    pan = [0.0, 0.0]
    region = (1440, 810)

    def derive(self, parent, kind, operation, settings=None, title=""):
        asset = self.catalog.derive(parent, kind, operation, settings, title)
        self.selected = {asset.id}
        self.order.append(asset.id)
        return asset

    def output_directory(self):
        return output

    def card_rect(self, path):
        return (0.0, 0.0, 300.0, 300.0)


board = FakeBoard()
canvas.BOARD = board
canvas.store_selection = lambda context, paths: None
canvas.save_layout = lambda context: None


def add(identifier, kind, parent="", artifact=dummy, native=""):
    asset = Asset(identifier, kind, parent=parent, artifact=str(artifact),
                  native_artifact=str(native) if native else "", state="READY")
    board.catalog.assets[identifier] = asset
    return asset


mesh1_artifact = temporary / "mesh1.glb"
mesh1_artifact.write_bytes(b"mesh1")
mesh2_artifact = temporary / "mesh2.glb"
mesh2_artifact.write_bytes(b"mesh2")
add("img1", "IMAGE")
add("img2", "IMAGE")
add("splat1", "SPLAT", parent="img1")
add("splat2", "SPLAT", parent="img2")
add("mesh1", "MESH", parent="splat1", artifact=mesh1_artifact, native=dummy)
add("mesh2", "MESH", parent="splat2", artifact=mesh2_artifact, native=dummy)

# Group target resolution and compatibility checks.
board.selected = {"img1", "img2"}
assert [asset.id for asset in toolbar.chosen_assets(board)] == ["img1", "img2"]
assert [asset.id for asset in toolbar.chosen_assets(board, "img1")] == ["img1"]
assert [asset.id for asset in toolbar.chosen_assets(board, "mesh1")] == ["mesh1"]
ready, reason = toolbar.enabled(board, toolbar.BY_ID["generate3d"], bpy.context)
assert ready and "2 selected" in reason, reason
board.selected = {"splat1", "splat2"}
ready, reason = toolbar.enabled(board, toolbar.BY_ID["mesh"], bpy.context)
assert ready and "2 selected" in reason, reason
board.selected = {"mesh1", "mesh2"}
for identifier in ("flow", "texture", "add3d", "export"):
    ready, reason = toolbar.enabled(board, toolbar.BY_ID[identifier], bpy.context)
    assert ready, (identifier, reason)
ready, reason = toolbar.enabled(board, toolbar.BY_ID["editmesh"], bpy.context)
assert not ready and "one mesh" in reason, reason
board.selected = {"img1", "mesh1"}
ready, reason = toolbar.enabled(board, toolbar.BY_ID["generate3d"], bpy.context)
assert not ready and "only" in reason, reason

# Generate one queued 3D source for each selected image.
with mock.patch.object(canvas, "generation_options", return_value={"seed": 1}):
    board.selected = {"img1", "img2"}
    assert canvas.start_splat(bpy.context)
generated = [asset for asset in board.catalog.assets.values()
             if asset.operation == "GENERATE_3D"]
assert len(generated) == 2 and {asset.parent for asset in generated} == {"img1", "img2"}
assert all(asset.state == "QUEUED" for asset in generated)
assert board.selected == {asset.id for asset in generated}

# Create one queued mesh for each selected 3D source.
bpy.context.scene.remi_generation.mesh_output = str(temporary / "meshes")
with mock.patch.object(canvas, "generation_options", return_value={"python": ""}):
    board.selected = {"splat1", "splat2"}
    created = canvas.create_meshes(bpy.context, ["splat1", "splat2"])
assert len(created) == 2
assert {asset.parent for asset in created} == {"splat1", "splat2"}
assert all(asset.operation == "CREATE_MESH" and asset.state == "QUEUED" for asset in created)
assert board.selected == {asset.id for asset in created}

# Queue one texture bake for each selected mesh, using each mesh's own source.
with mock.patch.object(canvas, "generation_options", return_value={"python": ""}):
    board.selected = {"mesh1", "mesh2"}
    textured = canvas.queue_textures(bpy.context, ["mesh1", "mesh2"])
assert len(textured) == 2
assert {asset.parent for asset in textured} == {"mesh1", "mesh2"}
assert all(asset.operation == "TEXTURE" and asset.state == "QUEUED" for asset in textured)
assert board.selected == {asset.id for asset in textured}

# Group export copies each artifact and avoids collisions on a second export.
export_dir = temporary / "exports"
exported = canvas.export_assets([board.catalog.assets["mesh1"], board.catalog.assets["mesh2"]],
                                export_dir)
assert [path.name for path in exported] == ["mesh1.glb", "mesh2.glb"]
assert exported[0].read_bytes() == b"mesh1" and exported[1].read_bytes() == b"mesh2"
again = canvas.export_assets([board.catalog.assets["mesh1"], board.catalog.assets["mesh2"]],
                             export_dir)
assert [path.name for path in again] == ["mesh1-2.glb", "mesh2-2.glb"]

canvas.BOARD = None
remi.unregister()
shutil.rmtree(temporary, ignore_errors=True)
print("REMI_BOARD_GROUP_ACTIONS_REGRESSION_OK", flush=True)
