"""Native quad connectivity, seam/material snapshots and same-card transactions."""

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
import remi
from remi.features.board import canvas, editing, native_assets
from remi.features.board.assets import Asset, Catalog


def geometry(mesh):
    return {"vertices": [tuple(v.co) for v in mesh.vertices],
            "edges": [tuple(e.vertices) for e in mesh.edges],
            "faces": [tuple(p.vertices) for p in mesh.polygons],
            "uvs": [[tuple(loop.uv) for loop in uv.data] for uv in mesh.uv_layers],
            "materials": [p.material_index for p in mesh.polygons],
            "smooth": [p.use_smooth for p in mesh.polygons],
            "seams": [e.use_seam for e in mesh.edges]}


def ids():
    return {name: set(getattr(bpy.data, name)) for name in native_assets.DATA}


def image_nodes(obj):
    return [node.image for mat in obj.data.materials if mat and mat.node_tree
            for node in mat.node_tree.nodes if node.type == "TEX_IMAGE"]


remi.register()
try:
    with tempfile.TemporaryDirectory(prefix="remi-native-topology-") as temporary:
        output = Path(temporary)
        os.environ["REMI_BOARD_LAYOUT"] = str(output / "layout.json")
        owner = bpy.context.scene
        owner.remi_image.output = str(output)
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_cube_add()
        source = bpy.context.object
        source.name = "Seamed quad cube"
        for edge in source.data.edges:
            edge.use_seam = True
        for index in range(2):
            material = bpy.data.materials.new(f"Quad material {index}")
            source.data.materials.append(material)
        for polygon in source.data.polygons:
            polygon.material_index = polygon.index % 2
        image = bpy.data.images.new("Painted texture", width=4, height=4)
        image.filepath_raw, image.file_format = str(output / "paint.png"), "PNG"
        image.pixels[:] = [0.0, 0.0, 0.0, 1.0] * 16
        image.save()
        # Deliberately leave painted pixels dirty: disk still contains black.
        painted = np.tile([.8, .2, .1, 1.0], 16).astype(np.float32)
        image.pixels.foreach_set(painted)
        painted = np.array(image.pixels, dtype=np.float32)
        source.data.materials[0].node_tree.nodes.new("ShaderNodeTexImage").image = image
        group = bpy.data.node_groups.new("Nested snapshot shader", "ShaderNodeTree")
        group.nodes.new("ShaderNodeTexImage").image = image
        for material in source.data.materials:
            material.node_tree.nodes.new("ShaderNodeGroup").node_tree = group
        source.data.materials[0].node_tree.nodes.new("ShaderNodeTexCoord").object = source
        expected = geometry(source.data)
        assert len(expected["vertices"]) == 8 and len(expected["faces"]) == 6
        portable = output / "original.glb"
        editing._export_glb(portable)
        before = ids()
        snapshot = native_assets.write(bpy.context, [source], output / "quad.blend")
        assert ids() == before, "Writing a snapshot leaked or removed Blender data"
        assert geometry(source.data) == expected
        assert not image.packed_file, "Snapshot packing changed the live paint image"

        # Reproduce the reported failure, then prove exact native connectivity.
        triangles = editing.import_working_mesh(bpy.context, portable)
        assert len(triangles) == 1
        assert len(triangles[0].data.polygons) == 12
        assert len(triangles[0].data.vertices) > 8, "Fixture did not reproduce attribute-split vertices"
        restored = native_assets.read(bpy.context, snapshot)[0]
        assert geometry(restored.data) == expected
        assert restored.data != source.data and restored.data.materials[0] != source.data.materials[0]
        restored_groups = [next(node.node_tree for node in material.node_tree.nodes if node.type == "GROUP")
                           for material in restored.data.materials]
        assert restored_groups[0] == restored_groups[1] and restored_groups[0] != group
        assert next(node.object for node in restored.data.materials[0].node_tree.nodes if node.type == "TEX_COORD") == restored
        packed = image_nodes(restored)[0]
        assert packed != image and packed.packed_file
        np.testing.assert_allclose(np.array(packed.pixels), painted, atol=.005)
        again = native_assets.read(bpy.context, snapshot)[0]
        assert again.data != restored.data
        assert image_nodes(again)[0] != packed, "Separate scene instances shared editable image data"
        restored.data.vertices[0].co.x += 5
        packed.pixels[0] = 0
        assert geometry(again.data) == expected
        np.testing.assert_allclose(np.array(image_nodes(again)[0].pixels), painted, atol=.005)
        np.testing.assert_allclose(np.array(image.pixels), painted, atol=1e-6)

        # Evaluated modifiers, transforms and intentionally overlapping parts.
        source.location = (1, 2, 3)
        source.scale = (2, 1, .5)
        modifier = source.modifiers.new("Keep editable subdivision", "SUBSURF")
        modifier.levels = 1
        bpy.context.view_layer.update()
        evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        evaluated_geometry = geometry(evaluated.data)
        multi = native_assets.write(bpy.context, [source, again], output / "parts.blend")
        parts = native_assets.read(bpy.context, multi)
        assert len(parts) == 2
        subdivided = next(part for part in parts if len(part.data.polygons) == 24)
        assert geometry(subdivided.data) == evaluated_geometry
        np.testing.assert_allclose(np.array(subdivided.matrix_world), np.array(source.matrix_world), atol=1e-6)
        assert not subdivided.modifiers and len(source.modifiers) == 1
        assert len(source.data.vertices) == 8

        # Exercise actual Apply -> Add to Scene, not only the storage helper.
        for obj in list(owner.objects):
            if obj != source:
                bpy.data.objects.remove(obj, do_unlink=True)
        area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
        with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
            board = canvas.Board(bpy.context)
        canvas.BOARD = board
        asset = Asset("quad-card", "MESH", artifact=str(portable), title="Quad cube")
        board.catalog.assets[asset.id] = asset
        canvas.save_layout(bpy.context)
        scene = bpy.data.scenes.new("Native quad editing")
        scene[editing.EDIT_OWNER], scene[editing.EDIT_ASSET], scene[editing.EDIT_BASE] = owner, asset.id, asset.artifact
        for collection in list(source.users_collection):
            collection.objects.unlink(source)
        scene.collection.objects.link(source)
        bpy.context.window.scene = scene
        bpy.context.view_layer.objects.active = source
        source.select_set(True)
        applied = editing.apply_to_card(bpy.context)
        assert applied is asset and asset.native_artifact and asset.mesh_source().endswith(".blend")
        assert len(asset.revisions) == 1 and asset.revisions[0]["artifact"] == str(portable)
        first = deepcopy(asset.document())
        hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in asset.files()}
        for target, name in ((native_assets, "write"), (canvas, "save_layout")):
            with mock.patch.object(target, name, side_effect=OSError("injected snapshot/catalog failure")):
                try:
                    editing.apply_to_card(bpy.context)
                except OSError:
                    pass
                else:
                    raise AssertionError("Failed snapshot/catalog write was accepted")
            assert asset.document() == first
        assert len(list((output / ".remi-board").glob("edit_*"))) == 1, "Failed Apply left partial artifacts"
        bpy.context.window.scene = owner
        assert canvas.add_asset_to_scene(bpy.context, asset.id)
        instance = bpy.context.active_object
        assert geometry(instance.data) == evaluated_geometry
        assert all(len(p.vertices) == 4 for p in instance.data.polygons)
        assert instance.data != source.data
        assert len(source.modifiers) == 1
        assert canvas.add_asset_to_scene(bpy.context, asset.id) and bpy.context.active_object == instance

        # Force a fresh edit, without the cached scene that hid the GLB bug.
        bpy.data.scenes.remove(scene)
        fresh = editing.open_edit_scene(bpy.context, asset.id)
        assert fresh != owner and len(fresh.objects) == 1
        assert geometry(bpy.context.active_object.data) == evaluated_geometry
        editing.apply_to_card(bpy.context)
        assert len(asset.revisions) == 2
        assert asset.revisions[-1]["native_artifact"] == first["native_artifact"]
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in hashes.items())
        recovered = Catalog(json.loads(json.dumps(board.catalog.documents()))).assets[asset.id]
        assert recovered.native_artifact == asset.native_artifact
        assert recovered.files() >= {first["native_artifact"], asset.native_artifact, str(portable)}
        # Applying with the board unmounted preserves scene-local organization,
        # even if another gallery window has since overwritten the layout file.
        group = board.organization.create({asset.id}, title="Native edits")
        asset.label = "Named quad cube"
        with bpy.context.temp_override(scene=owner, view_layer=owner.view_layers[0]):
            canvas.save_layout(bpy.context)
            canvas.write_layout(bpy.context, {"assets": board.catalog.documents()})
        owner[canvas.SCENE_ASSETS_KEY] = json.dumps({"gallery": str(output.resolve()),
            "assets": board.catalog.documents(), "organization": {"groups": board.organization.documents(),
            "membership": dict(board.organization.membership)}})
        canvas.BOARD = None
        editing.apply_to_card(bpy.context)
        saved = json.loads(owner[canvas.SCENE_ASSETS_KEY])
        assert saved["organization"]["membership"][asset.id] == group.id
        assert Catalog(saved["assets"]).assets[asset.id].label == "Named quad cube"
        canvas.BOARD = board
        bpy.context.window.scene = owner
        # A malformed snapshot must not leak appended data or masquerade as GLB.
        bad = bpy.data.collections.new(native_assets.COLLECTION + ":invalid")
        bad[native_assets.SCHEMA_KEY] = 99
        invalid = output / "invalid.blend"
        bpy.data.libraries.write(str(invalid), {bad})
        before = ids()
        try:
            native_assets.read(bpy.context, invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("An invalid native snapshot was accepted")
        assert ids() == before
        asset.native_artifact = str(output / "missing.blend")
        try:
            editing.import_working_mesh(bpy.context, asset.mesh_source())
        except ValueError:
            pass
        else:
            raise AssertionError("A missing native snapshot silently fell back to triangulated GLB")
        assert ids() == before
        print("REMI_NATIVE_TOPOLOGY_EXACT_QUADS_UVS_MATERIALS_IMAGES_OK", flush=True)
        print("REMI_NATIVE_TOPOLOGY_MODIFIERS_PARTS_REVISIONS_ROLLBACK_OK", flush=True)
        canvas.BOARD = None
finally:
    canvas.BOARD = None
    remi.unregister()
print("REMI_BOARD_NATIVE_TOPOLOGY_REGRESSION_OK", flush=True)
