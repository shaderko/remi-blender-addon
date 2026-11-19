"""Board LOD action creates scene geometry, no export/bake jobs or new maps."""

import hashlib
from pathlib import Path
import tempfile
from unittest import mock

import bpy
import remi
from remi.features.board import canvas, jobs, lods, native_assets, toolbar
from remi.features.board.assets import Asset, Catalog, OBJECT_ASSET_KEY


remi.register()
temporary = Path(tempfile.mkdtemp(prefix="remi-board-lods-"))
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=24, y_subdivisions=24, size=2)
source = bpy.context.object
source.name = "Live source mesh"
image = bpy.data.images.new("Existing asset albedo", 256, 256)
image.generated_color = (.2, .6, .9, 1)
normal = bpy.data.images.new("Existing asset normal", 256, 256)
normal.generated_color = (.5, .5, 1, 1)
normal.colorspace_settings.name = "Non-Color"
material = bpy.data.materials.new("Existing material")
material.use_nodes = True
nodes, links = material.node_tree.nodes, material.node_tree.links
tex = nodes.new("ShaderNodeTexImage")
tex.image = image
links.new(tex.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
tex = nodes.new("ShaderNodeTexImage")
tex.image = normal
nmap = nodes.new("ShaderNodeNormalMap")
links.new(tex.outputs["Color"], nmap.inputs["Color"])
links.new(nmap.outputs["Normal"], nodes["Principled BSDF"].inputs["Normal"])
source.data.materials.append(material)
# This is a fixture source, not an output from LOD generation.
snapshot = native_assets.write(bpy.context, [source], temporary / "source.blend")
source_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()


class Board:
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    catalog = Catalog()
    selected = {"live"}


board = Board()
board.catalog.assets["live"] = Asset("live", "MESH", artifact=str(temporary / "not-exported.glb"),
    title="Live source", settings={"imported": {"scene": bpy.context.scene.name, "objects": [source.name]}})
canvas.BOARD = board
bpy.context.scene.remi_settings.lod_levels = 2
bpy.context.scene.remi_settings.lod_ratio = .5
assert toolbar.enabled(board, toolbar.BY_ID["lods"])[0]
assert not hasattr(bpy.context.scene.remi_settings, "lod_texture_size")
assert not hasattr(bpy.context.scene.remi_settings, "lod_output")
documents = board.catalog.documents()
image_ids, material_ids = set(bpy.data.images), set(bpy.data.materials)
files_before = set(temporary.rglob("*"))

with mock.patch.object(jobs.LocalBlenderJob, "start", side_effect=AssertionError("No LOD background export jobs")), \
     mock.patch.object(native_assets, "write", side_effect=AssertionError("No LOD file outputs")):
    result = lods.create(bpy.context, {"live"})[0]
assert len(result["objects"]) == 2 and result["sources"] == [source]
assert all(obj.data.materials[0] is material for obj in result["objects"])
assert board.selected == {"live"} and board.catalog.documents() == documents
assert set(bpy.data.images) == image_ids and set(bpy.data.materials) == material_ids
assert set(temporary.rglob("*")) == files_before
assert bpy.context.active_object is source

# A file-backed card not in the scene is appended once. All generated LODs
# reference that one imported source material and its images, not snapshots
# with duplicated images per level. A repeat reuses the tagged live source.
board.catalog.assets["file"] = Asset("file", "MESH", artifact=str(snapshot),
    native_artifact=str(snapshot), title="File source")
with mock.patch.object(jobs.LocalBlenderJob, "start", side_effect=AssertionError("No worker")), \
     mock.patch.object(native_assets, "write", side_effect=AssertionError("No output snapshots")):
    result2 = lods.create(bpy.context, {"file"})[0]
    source2 = result2["sources"][0]
    assert source2.get(OBJECT_ASSET_KEY) == "file"
    imported_images = set(bpy.data.images)
    imported_materials = set(bpy.data.materials)
    result3 = lods.create(bpy.context, {"file"})[0]
assert result3["sources"] == [source2]
assert set(bpy.data.images) == imported_images
assert set(bpy.data.materials) == imported_materials
assert all(obj.data.materials[0] is source2.data.materials[0]
           for obj in result2["objects"] + result3["objects"])
assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == source_hash
assert set(temporary.rglob("*")) == files_before

# The public toolbar action must create objects immediately, not queue cards.
board.selected = {"live"}
before_count = len(bpy.data.objects)
assert toolbar.run(bpy.context, "lods") == {"FINISHED"}
assert len(bpy.data.objects) == before_count + 2
assert not any(asset.state == "QUEUED" for asset in board.catalog.assets.values())
assert not any(asset.package_artifact for asset in board.catalog.assets.values())

canvas.BOARD = None
remi.unregister()
print("REMI_BOARD_LODS_REGRESSION_OK", flush=True)
