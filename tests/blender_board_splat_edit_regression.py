"""Isolated Gaussian editing, affine export and same-card Cloud synchronization."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

import bpy
import numpy as np
from mathutils import Matrix, Quaternion

ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    import importlib.util
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
import remi
from remi.blender import gaussian_splats
from remi.features.board import canvas, editing, toolbar
from remi.features.board.assets import Asset, Catalog
from remi.features.cloud import board_sync
from remi.integrations.triposplat.splat_data import load, save_native


def covariance(arrays):
    result = []
    for q, scale in zip(arrays["rotations"], arrays["scales"]):
        rotation = np.asarray(Quaternion(q).to_matrix(), dtype=np.float64)
        result.append(rotation @ np.diag(scale.astype(np.float64)**2) @ rotation.T)
    return np.asarray(result)


remi.register()
owner = bpy.context.scene
original_objects = set(owner.objects)
with tempfile.TemporaryDirectory(prefix="remi-splat-edit-") as temporary:
    root = Path(temporary)
    owner.remi_image.output = str(root / "images")
    os.environ["REMI_BOARD_LAYOUT"] = str(root / "layout.json")
    source = root / "cloud.splat"
    arrays = {"positions": np.array([[0, 0, 0], [1, 2, 3], [-1, -.5, .2]], np.float32),
              "scales": np.array([[.03, .06, .1]]*3, np.float32),
              "rotations": np.tile(np.array(Quaternion((1, 2, 3), .7)), (3, 1)).astype(np.float32),
              "opacities": np.array([.2, .6, 1], np.float32),
              "sh_dc": np.array([[.1, .2, .3]]*3, np.float32)}
    save_native(source, arrays)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    asset = Asset("cloud-gaussian", "SPLAT", artifact=str(source), parent="reference", links={"keep": "link"})
    invalidated = []
    board = SimpleNamespace(scene=owner, catalog=Catalog(), selected={asset.id}, runner=SimpleNamespace(preview_id="", preview_job=None),
                            invalidate_preview=invalidated.append, release_texture=lambda key: None)
    board.catalog.assets[asset.id] = asset
    canvas.BOARD = board
    try:
        with mock.patch.object(canvas, "save_layout") as saved, mock.patch.object(canvas, "store_selection"):
            scene = editing.open_edit_scene(bpy.context, asset.id)
            assert len(scene.objects) == 1 and set(owner.objects) == original_objects
            obj = bpy.context.view_layer.objects.active
            assert gaussian_splats.is_splat_object(obj) and editing.can_apply(bpy.context)
            assert toolbar.BY_ID["editmesh"].kinds == ("MESH", "SPLAT")
            # A native point attribute edit and a reflected/non-uniform affine transform.
            modified = arrays["positions"].copy();modified[1] += [0, .3, 0]
            obj.data.attributes["position"].data.foreach_set("vector", modified.ravel())
            obj.matrix_world = Matrix(((0, -1.5, .2, 2), (-2, 0, .1, -3), (0, 0, .5, 1), (0, 0, 0, 1)))
            bpy.context.view_layer.update()
            linear = np.asarray(obj.matrix_world)[:3,:3]
            expected = modified @ linear.T + np.asarray(obj.matrix_world)[:3,3]
            document = asset.document()
            with mock.patch.object(canvas, "save_layout", side_effect=OSError("write failed")):
                try: editing.apply_to_card(bpy.context)
                except OSError: pass
                else: raise AssertionError("Failed save was accepted")
            assert asset.document() == document and not list(root.rglob("edit_*"))
            updated = editing.apply_to_card(bpy.context)
            result = load(updated.artifact)
            np.testing.assert_allclose(result["positions"], expected, atol=1e-6)
            np.testing.assert_allclose(covariance(result), linear @ covariance(arrays) @ linear.T, atol=1e-7)
            np.testing.assert_array_equal(result["opacities"], arrays["opacities"])
            np.testing.assert_array_equal(result["sh_dc"], arrays["sh_dc"])
            assert updated.id == asset.id and len(board.catalog.assets) == 1
            assert asset.parent == "reference" and asset.links == {"keep": "link"}
            assert asset.revisions[0]["artifact"] == str(source) and asset.native_artifact == ""
            assert saved.called and invalidated == [asset.id]
            assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
            # Reopening and applying again must not compound the object transform.
            assert editing.open_edit_scene(bpy.context, asset.id) == scene and len(scene.objects) == 1
            second = load(editing.apply_to_card(bpy.context).artifact)
            np.testing.assert_array_equal(second["positions"], result["positions"])
            assert len(asset.revisions) == 2
            bpy.ops.object.mode_set(mode="EDIT")
            assert bpy.context.mode == "EDIT_POINTCLOUD" and editing.can_apply(bpy.context)
            bpy.ops.pointcloud.select_all(action="SELECT")
            bpy.ops.transform.translate(value=(0, 0, .1))
            editing.apply_to_card(bpy.context)
            assert bpy.context.mode == "OBJECT"
            assert not np.array_equal(load(asset.artifact)["positions"], second["positions"])

            # Existing Cloud same-card revision upload must see the changed file.
            value = {"base_url": "https://fixture.test/api/v1", "user_id": "fixture", "project_id": "default", "ready": True}
            sync = board_sync.BoardSync(board);sync.scope = board_sync.scope(value)
            asset.settings["cloud"] = {"scope": sync.scope, "card_id": "shared-card", "file_id": "old-file", "signature": "old-signature"}
            board.positions = {};board.order = [asset.id];board.scales = {}
            with mock.patch.object(board_sync, "current", return_value=value), mock.patch.object(sync, "command", return_value="upload-1") as command:
                sync.collect(bpy.context)
            args = command.call_args_list[0]
            assert args.args[1] == "asset" and args.kwargs["card_id"] == "shared-card"
            assert args.kwargs["path"] == asset.artifact and args.kwargs["expected_file_id"] == "old-file"
            # Exercise the production Alt-orbit mouse path for both card kinds.
            area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
            region = canvas.window_region(area)
            for name in ("pan_drag", "card_drag", "box_drag", "resize_drag", "link_drag", "group_drag"):
                setattr(board, name, None)
            board.text_drag = False;board.focused = False
            operator = SimpleNamespace(_board=board, _redraw=lambda context: None)
            for kind in ("SPLAT", "MESH"):
                asset.kind = kind
                board.orbit_drag = {"path": asset.id, "start": (100, 100), "yaw": 30, "elevation": 20}
                event = SimpleNamespace(type="MOUSEMOVE", value="NOTHING", ctrl=False, shift=False, alt=True, oskey=False,
                                        mouse_x=region.x+120, mouse_y=region.y+140)
                with mock.patch.object(canvas, "board_area", return_value=area), \
                     mock.patch.object(canvas, "ui_scale", return_value=1), \
                     mock.patch.object(canvas.toolbar, "handle_event", return_value=False), \
                     mock.patch.object(canvas.organizing, "handle_event", return_value=False):
                    result = canvas.REMI_BOARD_OT_run.modal(operator, bpy.context, event)
                assert result == {"RUNNING_MODAL"} and asset.elevation == 2 and asset.yaw == 21
            asset.kind = "SPLAT"
            # Concurrent card changes reject apply without discarding the edit.
            asset.artifact = str(source)
            try: editing.apply_to_card(bpy.context)
            except ValueError as error: assert "changed" in str(error)
            else: raise AssertionError("Stale edit overwrote the current card")
    finally:
        bpy.context.window.scene = owner
        canvas.BOARD = None
        for obj in list(bpy.data.objects):
            if obj not in original_objects: bpy.data.objects.remove(obj, do_unlink=True)
        for scene in list(bpy.data.scenes):
            if scene.get(editing.EDIT_ASSET): bpy.data.scenes.remove(scene)
        for cloud in list(bpy.data.pointclouds):
            if cloud.users == 0: bpy.data.pointclouds.remove(cloud)
        remi.unregister()
print("REMI_BOARD_SPLAT_EDIT_REGRESSION_OK")
