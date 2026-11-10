"""Real-window isolated Blender editing regression; no inference or downloads.

Run with Blender 5.3 --enable-event-simulate --python-exit-code 1 --python this.py.
"""

from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import traceback
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_board_edit_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, editing, workspace
from remi.features.board.assets import Asset, OBJECT_ASSET_KEY, OBJECT_REVISION_KEY, SCENE_ASSETS_KEY


window = bpy.context.window
owner_name = bpy.context.scene.name
original_screen_name = window.screen.name
original_areas = [area.type for area in window.screen.areas]
bpy.context.scene.remi_image.output = str(OUTPUT / "images")
bpy.ops.mesh.primitive_cube_add()
fixture = bpy.context.active_object
material = bpy.data.materials.new("Bridge fixture")
fixture.data.materials.append(material)
shader = material.node_tree.nodes.get("Principled BSDF")
for name, color in (("albedo", (0.6, 0.3, 0.1, 1)), ("normal", (0.5, 0.5, 1, 1))):
    image = bpy.data.images.new(name, 8, 8)
    image.generated_color = color
    image.filepath_raw = str(OUTPUT / (name + ".png"))
    image.file_format = "PNG"
    image.save()
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    if name == "albedo":
        material.node_tree.links.new(node.outputs["Color"], shader.inputs["Base Color"])
    else:
        image.colorspace_settings.name = "Non-Color"
        normal = material.node_tree.nodes.new("ShaderNodeNormalMap")
        material.node_tree.links.new(node.outputs["Color"], normal.inputs["Color"])
        material.node_tree.links.new(normal.outputs["Normal"], shader.inputs["Normal"])
bpy.ops.object.select_all(action="DESELECT")
fixture.select_set(True)
SOURCE = OUTPUT / "source.glb"
assert bpy.ops.export_scene.gltf(filepath=str(SOURCE), export_format="GLB", use_selection=True) == {"FINISHED"}
source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
bpy.data.objects.remove(fixture, do_unlink=True)

step = waits = 0
source_id = "bridge-mesh"
instance_name = edit_name = mesh_name = None
workspace_count = area_count = None
main_objects = None
first_artifact = first_hash = None
undo_phase = 0


def context_area(area=None):
    area = area or next(area for area in window.screen.areas if area.type == "VIEW_3D")
    return bpy.context.temp_override(window=window, area=area, region=canvas.window_region(area))


def simulate(key, *, ctrl=False, shift=False):
    area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region = canvas.window_region(area)
    x, y = region.x + region.width // 2, region.y + region.height // 2
    window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
    window.event_simulate(type=key, value="PRESS", unicode="1" if key == "ONE" else "", ctrl=ctrl, shift=shift, x=x, y=y)
    window.event_simulate(type=key, value="RELEASE", ctrl=ctrl, shift=shift, x=x, y=y)


def assert_source():
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_hash, "Source file was overwritten"
    owner = bpy.data.scenes[owner_name]
    instance = owner.objects[instance_name]
    assert abs(instance.location.x - 12) < 1e-5, "Existing scene instance was changed"
    assert set(owner.objects.keys()) == main_objects, "Mesh editing added objects to the main scene"


def advance():
    global step, waits, instance_name, edit_name, mesh_name, workspace_count, area_count, window, main_objects, first_artifact, first_hash, undo_phase
    waits += 1
    assert waits < 150, f"Timed out at step {step}"
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        if canvas.BOARD is None:
            return 0.2
        board = canvas.BOARD
        archive = OUTPUT / "appearance.npz"
        archive.write_bytes(b"source-identity-only")
        assets = [Asset("bridge-splat", "SPLAT", artifact=str(archive), preview=str(OUTPUT / "albedo.png"))]
        assets += [Asset(key, "MESH", artifact=str(SOURCE), parent="bridge-splat", title=key,
                         preview=str(OUTPUT / "albedo.png"), links={"texture_source": "bridge-splat"})
                   for key in (source_id, "second-mesh")]
        for asset in assets:
            board.catalog.assets[asset.id] = asset
            board.place_asset(asset.id, asset.parent)
            board.order.append(asset.id)
        canvas.save_layout(bpy.context)
        assert canvas.add_asset_to_scene(bpy.context, source_id)
        instance = bpy.context.view_layer.objects.active
        instance.location.x = 12
        instance_name = instance.name
        main_objects = set(bpy.context.scene.objects.keys())
        workspace_count, area_count = len(bpy.data.workspaces), len(window.screen.areas)
        with context_area(canvas.board_area(board)):
            assert bpy.ops.remi_board.edit_mesh(asset_id=source_id) == {"FINISHED"}
    elif step == 2:
        if editing.editing_scene(bpy.context) is None:
            return 0.2
        scene = bpy.context.scene
        edit_name = scene.name
        assert not window.screen.show_fullscreen
        assert window.workspace.get(editing.EDIT_WORKSPACE)
        assert any(area.type == "PROPERTIES" for area in window.screen.areas), "Not a normal Blender layout"
        assert editing.source_scene(scene) == bpy.data.scenes[owner_name]
        assert len(scene.objects) == 1 and not bpy.context.window_manager.remi_session.active
        assert not set(scene.objects) & set(bpy.data.scenes[owner_name].objects)
        obj = bpy.context.view_layer.objects.active
        mesh_name = obj.name
        assert obj.data != bpy.data.objects[instance_name].data
        assert obj.data.materials[0] != bpy.data.objects[instance_name].data.materials[0]
        original_images = {node.image for node in bpy.data.objects[instance_name].data.materials[0].node_tree.nodes if node.type == "TEX_IMAGE"}
        edit_images = {node.image for node in obj.data.materials[0].node_tree.nodes if node.type == "TEX_IMAGE"}
        assert not original_images & edit_images, "Image editing would affect the main scene's material"
        assert obj["remi_mesh_source_archive"].endswith("appearance.npz")
        assert len(obj.data.uv_layers)
        assert_source()
        for key in ("G", "X", "ONE", "RET"):
            simulate(key)
    elif step == 3:
        assert abs(bpy.data.objects[mesh_name].location.x - 1) < 1e-5, "Normal Blender transform was blocked"
        simulate("Z", ctrl=True)
    elif step == 4:
        if undo_phase == 0:
            assert abs(bpy.data.objects[mesh_name].location.x) < 1e-5
            simulate("Z", ctrl=True, shift=True)
            undo_phase = 1
            return 0.3
        if undo_phase == 1:
            assert abs(bpy.data.objects[mesh_name].location.x - 1) < 1e-5, "Normal Blender Redo was blocked"
            simulate("Z", ctrl=True)
            undo_phase = 2
            return 0.3
        assert abs(bpy.data.objects[mesh_name].location.x) < 1e-5, "Normal Blender Undo was blocked"
        assert not bpy.context.window_manager.remi_session.active
        assert canvas.BOARD.scene == editing.source_scene(bpy.context.scene), "Undo lost the board's source scene"
        with context_area():
            obj = bpy.context.view_layer.objects.active
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.transform.translate(value=(0.25, 0, 0))
            bpy.ops.object.mode_set(mode="OBJECT")
            bevel = obj.modifiers.new("Keep editable modifier", "BEVEL")
            bevel.width = 0.08
            bpy.ops.mesh.primitive_cube_add(size=0.5, location=(3, 0, 0))
            bpy.context.active_object.name = "Added part"
            previous = canvas.BOARD.catalog.assets[source_id].document()
            for target, name, error in ((editing, "_export_glb", RuntimeError("Export failed")),
                                        (canvas, "save_layout", OSError("Catalog write failed"))):
                with mock.patch.object(target, name, side_effect=error):
                    try:
                        editing.apply_to_card(bpy.context)
                    except type(error):
                        pass
                    else:
                        raise AssertionError("Failed apply was accepted")
                assert canvas.BOARD.catalog.assets[source_id].document() == previous
                assert editing.editing_scene(bpy.context) is not None and len(bpy.context.scene.objects) == 2
            assert_source()
            bpy.ops.screen.screenshot(filepath=str(OUTPUT / "editing.png"))
            assert bpy.ops.remi_board.apply_edit() == {"FINISHED"}
    elif step == 5:
        if window.scene.name != owner_name or canvas.board_area(canvas.BOARD) is None:
            return 0.2
        board = canvas.BOARD
        assert len(board.catalog.assets) == 3, "Apply added an unwanted new card"
        asset = board.catalog.assets[source_id]
        assert asset.parent == "bridge-splat" and asset.links["texture_source"] == "bridge-splat"
        assert asset.revisions[0]["artifact"] == str(SOURCE) and board.selected == {source_id}
        assert str(SOURCE) in asset.files()
        data = Path(asset.artifact).read_bytes()
        length, kind = struct.unpack_from("<II", data, 12)
        document = json.loads(data[20:20+length])
        assert kind == 0x4E4F534A and len(document["images"]) == 2 and len(document["meshes"]) == 2
        assert all("bufferView" in image and "uri" not in image for image in document["images"])
        assert "TEXCOORD_0" in document["meshes"][0]["primitives"][0]["attributes"]
        assert "normalTexture" in document["materials"][0]
        assert len(bpy.data.scenes[edit_name].objects[mesh_name].modifiers) == 1
        first_artifact, first_hash = asset.artifact, hashlib.sha256(data).hexdigest()
        assert_source()
        # An explicit Add to Scene imports the new parts without replacing the old instance.
        assert canvas.add_asset_to_scene(bpy.context, source_id)
        assert len(bpy.context.selected_objects) == 2
        assert all(obj[OBJECT_REVISION_KEY] == asset.artifact for obj in bpy.context.selected_objects)
        main_objects = set(bpy.context.scene.objects.keys())
        assert_source()
        with context_area(canvas.board_area(board)):
            bpy.ops.screen.screen_full_area()
    elif step == 6:
        assert window.screen.show_fullscreen and window.screen.areas[0].type == "IMAGE_EDITOR"
        with context_area(window.screen.areas[0]):
            assert bpy.ops.remi_board.edit_mesh(asset_id="second-mesh") == {"FINISHED"}
    elif step == 7:
        if editing.editing_scene(bpy.context) is None:
            return 0.2
        assert bpy.context.scene[editing.EDIT_ASSET] == "second-mesh" and bpy.context.scene.name != edit_name
        assert len(bpy.context.scene.objects) == 1
        assert len(bpy.data.workspaces) == workspace_count + 1, "Created duplicate editing workspaces"
        with context_area():
            assert bpy.ops.remi_board.return_to_board() == {"FINISHED"}
    elif step == 8:
        if window.scene.name != owner_name or canvas.board_area(canvas.BOARD) is None:
            return 0.2
        with context_area(canvas.board_area(canvas.BOARD)):
            assert bpy.ops.remi_board.edit_mesh(asset_id=source_id) == {"FINISHED"}
    elif step == 9:
        if window.scene.name != edit_name:
            return 0.2
        assert len(window.scene.objects) == 2, "Reopening discarded an edit or imported duplicates"
        with context_area():
            bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "editing.blend"))
            bpy.ops.wm.open_mainfile(filepath=str(OUTPUT / "editing.blend"))
    elif step == 10:
        window = bpy.context.window
        assert window.scene.name == edit_name and editing.editing_scene(bpy.context) is not None
        assert canvas.BOARD is None, "Reload mounted the board onto the editing scene"
        with context_area():
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.transform.translate(value=(0, 0, 0.1))
            assert bpy.ops.remi_board.apply_edit() == {"FINISHED"}
    elif step == 11:
        if canvas.BOARD is None or canvas.board_area(canvas.BOARD) is None or window.scene.name != owner_name:
            return 0.2
        asset = canvas.BOARD.catalog.assets[source_id]
        assert len(asset.revisions) == 2 and asset.revisions[-1]["artifact"] == first_artifact
        assert hashlib.sha256(Path(first_artifact).read_bytes()).hexdigest() == first_hash
        assert len(canvas.BOARD.catalog.assets) == 3 and len(window.screen.areas) == area_count
        assert len(bpy.data.workspaces) == workspace_count + 1
        assert_source()
        if not asset.preview:
            assert not asset.status.startswith("Preview unavailable"), asset.status
            return 0.2
        assert Path(asset.preview).is_file(), "Multi-part card preview was not refreshed"
        assert [area.type for area in bpy.data.screens[original_screen_name].areas] == original_areas
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "finished.png"))
        remi.unregister()
        assert not bpy.app.timers.is_registered(editing._resume)
        (OUTPUT / "passed.txt").write_text("Isolated per-card scenes, ordinary Blender transforms/Undo/Edit Mode, modifiers, added parts, same-card apply, failure rollback, backups, Add to Scene, refreshed previews, maximize/return/resume and reload passed.\n")
        print(f"REMI_BOARD_EDITING_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return 0.7


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        print(f"REMI_BOARD_EDITING_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=0.5, persistent=True)
