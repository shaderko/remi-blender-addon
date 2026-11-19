"""Board mesh bake preserves all four maps in PNGs, native assets and GLB.

REMI_MESH_BAKE_INSTALLED=1 exercises the packaged extension and its worker.
"""

import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time
from unittest import mock

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
installed = os.environ.get("REMI_MESH_BAKE_INSTALLED") == "1"
package = "bl_ext.user_default.remi" if installed else "remi"
if installed:
    assert bpy.ops.preferences.addon_enable(module=package) == {"FINISHED"}
elif package not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        package, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[package] = module
    spec.loader.exec_module(module)

remi = importlib.import_module(package)
canvas = importlib.import_module(package + ".features.board.canvas")
jobs = importlib.import_module(package + ".features.board.jobs")
native_assets = importlib.import_module(package + ".features.board.native_assets")
assets = importlib.import_module(package + ".features.board.assets")
Asset, Catalog = assets.Asset, assets.Catalog

if not installed:
    remi.register()

temporary = Path(tempfile.mkdtemp(prefix="remi-mesh-bake-"))
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=1.0)
source_object = bpy.context.active_object
source_object.name = "BakeSource"
material = bpy.data.materials.new("BakeSourceMaterial")
material.use_nodes = True
material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1, 0.4, 0.8, 1.0)
source_roughness = 0.23  # Deliberately not Blender's default (0.5).
material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = source_roughness
source_object.data.materials.append(material)
bpy.ops.object.select_all(action="DESELECT")
source_object.select_set(True)
bpy.context.view_layer.objects.active = source_object
source_glb = temporary / "source.glb"
bpy.ops.export_scene.gltf(filepath=str(source_glb), export_format="GLB",
                          use_selection=True, export_apply=True)

bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, radius=0.92)
target_object = bpy.context.active_object
target_object.name = "BakeTarget"
bpy.ops.object.select_all(action="DESELECT")
target_object.select_set(True)
bpy.context.view_layer.objects.active = target_object
target_glb = temporary / "target.glb"
bpy.ops.export_scene.gltf(filepath=str(target_glb), export_format="GLB",
                          use_selection=True, export_apply=True)


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
    bar_rect = (0.0, 0.0, 0.0, 0.0)

    def derive(self, parent, kind, operation, settings=None, title=""):
        asset = self.catalog.derive(parent, kind, operation, settings, title)
        self.selected = {asset.id}
        self.order.append(asset.id)
        return asset

    def output_directory(self):
        return temporary

    def card_rect(self, path):
        return (0.0, 0.0, 300.0, 300.0)


board = FakeBoard()
canvas.BOARD = board
canvas.store_selection = lambda context, paths: None
canvas.save_layout = lambda context: None
canvas.board_area = lambda board: None

source = Asset("source", "MESH", artifact=str(source_glb), state="READY",
               title="Source Mesh")
target = Asset("target", "MESH", artifact=str(target_glb), state="READY",
               title="Target Mesh")
board.catalog.assets[source.id] = source
board.catalog.assets[target.id] = target
board.positions[source.id] = [0.0, 0.0]
board.positions[target.id] = [330.0, 0.0]
target.links["texture_source"] = source.id
assert canvas.texture_source(board, target) is source

board.hit = lambda mx, my, exclude=None: target.id
board.selected = {source.id}
board.card_drag = {"path": source.id, "moved": True, "move_only": False}
with mock.patch.object(canvas.toolbar, "hit", return_value=None):
    dropped = canvas.texture_drop_target(board, 100.0, 100.0)
assert dropped == {"source": source.id, "target": target.id, "cursor": (100.0, 100.0)}
board.card_drag = None

bpy.context.scene.remi_texture.texture_size = "1024"
result = canvas.queue_texture(bpy.context, target.id)
assert result.operation == "TEXTURE" and result.state == "QUEUED"
assert result.parent == target.id and result.links["texture_source"] == source.id
assert result.settings["source_kind"] == "MESH"
assert result.settings["source_mesh"] == source.mesh_source()
assert result.settings["mesh_artifact"] == target.mesh_source()
assert board.selected == {result.id}


def foreground_state():
    return (bpy.context.scene, bpy.context.active_object,
            [(obj, obj.data, obj.select_get(), tuple(tuple(row) for row in obj.matrix_world),
              tuple(obj.data.materials)) for obj in bpy.context.scene.objects if obj.type == "MESH"],
            set(bpy.data.images), set(bpy.data.materials))


def check_glb(path):
    data = path.read_bytes()
    assert struct.unpack_from("<4sII", data) == (b"glTF", 2, len(data))
    length, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    document = json.loads(data[20:20+length])
    assert len(document["materials"]) == 1, document["materials"]
    exported = document["materials"][0]
    pbr = exported["pbrMetallicRoughness"]
    for info in (pbr["baseColorTexture"], pbr["metallicRoughnessTexture"],
                 exported["normalTexture"], exported["occlusionTexture"]):
        image = document["images"][document["textures"][info["index"]]["source"]]
        assert "uri" not in image and document["bufferViews"][image["bufferView"]]["byteLength"] > 20, image


before = foreground_state()
input_bytes = source_glb.read_bytes(), target_glb.read_bytes()
input_cards = source.document(), target.document()
runner = jobs.Runner()
runner.start(bpy.context, board.catalog, result)
job = runner.job
try:
    deadline = time.monotonic() + 600
    while job.poll() is None and time.monotonic() < deadline:
        time.sleep(0.5)
    assert job.poll() is not None, "Mesh bake job timed out"
    try:
        artifact = job.result()
    except Exception:
        print((job.directory / "worker.log").read_text(encoding="utf-8", errors="replace"), flush=True)
        raise
    assert artifact.is_file() and artifact.suffix == ".glb" and artifact.stat().st_size > 20
    native = artifact.with_name("asset.blend")
    assert native.is_file(), native
    runner.tick(bpy.context, board.catalog)
    assert result.state == "READY", result.status
    assert result.artifact == str(artifact) and result.native_artifact == str(native)
    assert foreground_state() == before, "The isolated bake changed the foreground scene"
    assert (source_glb.read_bytes(), target_glb.read_bytes()) == input_bytes
    assert (source.document(), target.document()) == input_cards
    assert abs(material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value-source_roughness) < 1e-6
    maps = ("diffuse", "roughness", "normal", "ao")
    for channel in maps:
        path = artifact.with_name("textures") / f"bake_{channel}.png"
        assert path.is_file() and path.stat().st_size > 20, path
        image = bpy.data.images.load(str(path), check_existing=False)
        if channel != "diffuse":
            image.colorspace_settings.name = "Non-Color"
        assert image.size[:] == (1024, 1024), (channel, image.size[:])
        pixels = np.array(image.pixels[:], dtype=np.float32).reshape(-1, 4)
        assert np.isfinite(pixels).all() and pixels[:, :3].max() > 0.1, channel
        if channel == "roughness":
            covered = pixels[:, 0] > 0.05
            assert covered.mean() > 0.1, "Roughness bake was almost empty"
            assert abs(float(np.median(pixels[covered, 0]))-source_roughness) < 0.02
        bpy.data.images.remove(image)
    check_glb(artifact)
    loaded = native_assets.read(bpy.context, native)
    assert len(loaded) == 1
    baked_material = loaded[0].data.materials[0]
    nodes = baked_material.node_tree.nodes
    for channel in maps:
        image = nodes[f"bake_{channel}"].image
        assert image is not None and image.packed_file is not None, channel
        assert image.size[:] == (1024, 1024), channel
        if channel != "diffuse":
            assert image.colorspace_settings.name == "Non-Color", channel
    bsdf = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
    assert bsdf.inputs["Base Color"].links[0].from_node == nodes["bake_diffuse"]
    assert bsdf.inputs["Roughness"].links[0].from_node == nodes["bake_roughness"]
    normal_map = bsdf.inputs["Normal"].links[0].from_node
    assert normal_map.type == "NORMAL_MAP" and normal_map.inputs["Color"].links[0].from_node == nodes["bake_normal"]
    ao_output = nodes["bake_ao_output"]
    assert ao_output.node_tree.name.startswith("glTF Material Output")
    assert ao_output.inputs["Occlusion"].links[0].from_node == nodes["bake_ao"]
    assert all(link.to_node == ao_output for link in nodes["bake_ao"].outputs["Color"].links)
    # Copying the exporter node group into an editable snapshot must not lose AO
    # on a later export, even after all loose PNGs have gone away.
    shutil.rmtree(artifact.with_name("textures"))
    reexport = temporary / "native-reexport.glb"
    bpy.ops.export_scene.gltf(filepath=str(reexport), export_format="GLB", use_selection=True)
    check_glb(reexport)
    print("BAKE ARTIFACT", artifact, flush=True)
finally:
    if job.poll() is None:
        job.cancel()

canvas.BOARD = None
if installed:
    assert bpy.ops.preferences.addon_disable(module=package) == {"FINISHED"}
else:
    remi.unregister()
shutil.rmtree(temporary, ignore_errors=True)
print("REMI_BOARD_MESH_BAKE_REGRESSION_OK", flush=True)
