"""Add selected scene objects as file-backed board cards; no UI required."""

import importlib.util
import shutil
import sys
import tempfile
import time
from pathlib import Path

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = module
    spec.loader.exec_module(module)

import remi
from remi.blender import gaussian_splats
from remi.features.board import canvas, native_assets, toolbar
from remi.features.board.assets import Catalog
from remi.integrations.triposplat.splat_data import load, save

remi.register()

# Do not write the user's board layout or touch the mounted board during this test.
canvas.save_layout = lambda context: None
canvas.store_selection = lambda context, paths: None
canvas.board_area = lambda board: None

temporary = Path(tempfile.mkdtemp(prefix="remi-board-add-object-"))


class FakeBoard:
    scene = bpy.context.scene
    catalog = Catalog()
    positions = {}
    scales = {}
    order = []
    selected = set()
    zoom = .7
    pan = [-7200.0, 4300.0]
    region = (1440, 810)

    def output_directory(self):
        return temporary

    def place_asset(self, identifier, parent=""):
        self.positions[identifier] = [0.0, 0.0]
        self.scales[identifier] = 1.0

    scale_of = canvas.Board.scale_of
    card_rect = canvas.Board.card_rect


board = FakeBoard()
canvas.BOARD = board

# A selected mesh with a modifier and material must be snapshotted, not modified.
bpy.ops.object.select_all(action="DESELECT")
bpy.ops.mesh.primitive_cube_add(location=(1, 2, 3))
mesh_object = bpy.context.active_object
mesh_object.name = "Board import cube"
modifier = mesh_object.modifiers.new("Bevel", "BEVEL")
modifier.width = 0.1
material = bpy.data.materials.new("Board import material")
mesh_object.data.materials.append(material)
mesh_object.select_set(True)
bpy.context.view_layer.objects.active = mesh_object

ready, reason = toolbar.enabled(board, toolbar.BY_ID["addobject"], bpy.context)
assert ready, reason
created = canvas.add_selected_objects(bpy.context)
assert len(created) == 1
mesh_asset = created[0]
assert board.pan == [-7200.0, 4300.0], "Adding a card to free view space changed the pan"
x, y, w, h = board.card_rect(mesh_asset.id)
assert 80 <= x and x+w <= board.region[0]-24 and 150 <= y and y+h <= board.region[1]-24
assert abs(board.positions[mesh_asset.id][0]) > 9000, "Object was placed back at the origin"
assert mesh_asset.kind == "MESH" and mesh_asset.state == "READY"
assert Path(mesh_asset.artifact).suffix == ".glb" and Path(mesh_asset.artifact).is_file()
assert Path(mesh_asset.native_artifact).suffix == ".blend" and Path(mesh_asset.native_artifact).is_file()
assert mesh_asset.mesh_source() == mesh_asset.native_artifact
assert board.selected == {mesh_asset.id}
assert mesh_object.name == "Board import cube"
assert mesh_object.modifiers.get("Bevel") is not None
assert mesh_object.data.materials.get("Board import material") is not None
assert Catalog([mesh_asset.document()]).assets[mesh_asset.id].mesh_source() == mesh_asset.native_artifact

# The native snapshot must be readable as independent mesh parts.
read_back = native_assets.read(bpy.context, mesh_asset.native_artifact)
assert read_back and all(item.type == "MESH" for item in read_back)
for item in read_back:
    bpy.data.objects.remove(item, do_unlink=True)
for mesh in list(bpy.data.meshes):
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)

# The existing card preview worker must accept the new native snapshot.
from remi.features.board.jobs import LocalBlenderJob

preview_job = LocalBlenderJob.start(
    output=temporary,
    request={"operation": "PREVIEW", "input": mesh_asset.mesh_source(),
             "yaw": 0, "elevation": 20},
)
try:
    deadline = time.monotonic() + 120
    while preview_job.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    assert preview_job.poll() is not None, "Board preview worker timed out"
    preview = preview_job.result()
    assert preview.is_file() and preview.name == "preview.png"
finally:
    if preview_job.poll() is None:
        preview_job.cancel()

# A native Gaussian object becomes a SPLAT card and keeps its exact arrays.
source = temporary / "splat_source.npz"
save(source, {
    "positions": np.array([[0, 0, 0], [0.2, 0, 0], [0, 0.2, 0]], np.float32),
    "scales": np.full((3, 3), 0.05, np.float32),
    "rotations": np.tile(np.array([1, 0, 0, 0], np.float32), (3, 1)),
    "opacities": np.ones(3, np.float32),
    "sh_dc": np.zeros((3, 3), np.float32),
})
splat_object = gaussian_splats.import_splats(bpy.context, source)
splat_object.name = "Board import splat"
bpy.ops.object.select_all(action="DESELECT")
splat_object.select_set(True)
bpy.context.view_layer.objects.active = splat_object

created = canvas.add_selected_objects(bpy.context)
assert len(created) == 1
splat_asset = created[0]
assert board.pan == [-7200.0, 4300.0]
a, b, c, d = board.card_rect(mesh_asset.id)
x, y, w, h = board.card_rect(splat_asset.id)
assert x+w <= a or x >= a+c or y+h <= b or y >= b+d, "New object overlaps an existing card"
assert 80 <= x and x+w <= board.region[0]-24 and 150 <= y and y+h <= board.region[1]-24
assert splat_asset.kind == "SPLAT" and splat_asset.state == "READY"
assert Path(splat_asset.artifact).is_file() and Path(splat_asset.artifact).suffix == ".npz"
np.testing.assert_allclose(
    load(Path(splat_asset.artifact))["positions"],
    np.array([[0, 0, 0], [0.2, 0, 0], [0, 0.2, 0]], np.float32),
)
assert board.selected == {splat_asset.id}

bpy.ops.object.select_all(action="DESELECT")
bpy.context.view_layer.objects.active = None
ready, reason = toolbar.enabled(board, toolbar.BY_ID["addobject"], bpy.context)
assert not ready and "Select" in reason

shutil.rmtree(temporary, ignore_errors=True)
canvas.BOARD = None
remi.unregister()
print("REMI_BOARD_ADD_OBJECT_REGRESSION_OK", flush=True)
