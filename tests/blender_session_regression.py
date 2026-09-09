"""Headless regressions for Remi's single-object session lifecycle."""

from pathlib import Path
import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Vector


ADDON_PARENT = Path(__file__).resolve().parents[2]
if str(ADDON_PARENT) not in sys.path:
    sys.path.insert(0, str(ADDON_PARENT))

import remi
from remi import baking
from remi import operators
from remi import session
from remi.features.retopology import autoremesher_service
from remi.infrastructure.blender import mesh_exchange
from remi.application import get_application
from remi.workflow.contracts import (
    FeatureAction,
    FeatureDescriptor,
    FeatureExecutionContext,
    FeatureUIContext,
    StageResult,
)
from remi.workflow.disk_service import SessionDiskService
from remi.workflow.registry import RegisteredAction


def action_for_command(command):
    return get_application().features.require_action(command)


def draw_feature(feature_id, layout, state):
    get_application().features.get(feature_id).draw(
        layout,
        FeatureUIContext(blender_context=bpy.context, state=state),
    )


class _StageFeatureAdapter:
    """Keep transaction tests focused while exercising the injected boundary."""

    def __init__(self, stage):
        self.stage = stage
        self.action = FeatureAction(
            id="TEST_ACTION",
            name=stage.label,
            requires_source_checkpoint=stage.requires_source_checkpoint,
            next_feature=stage.next_stage,
        )
        self.descriptor = FeatureDescriptor(
            id="TEST_FEATURE",
            name="Test Feature",
            icon="NONE",
            next_feature=stage.next_stage,
            actions=(self.action,),
        )

    def execute(
        self,
        _action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        candidate, error, report = self.stage.build(
            context.blender_context,
            context.source,
            context.working_copy,
            source_checkpoint=context.source_checkpoint,
            disk=context.disk,
        )
        if error:
            raise RuntimeError(error)
        return StageResult(candidate, report)


def action_for_stage(stage):
    feature = _StageFeatureAdapter(stage)
    return RegisteredAction(feature, feature.action)


class _UILayoutRecorder:
    def __init__(self, events=None):
        self.events = events if events is not None else []

    def _child(self):
        return _UILayoutRecorder(self.events)

    def box(self):
        return self._child()

    def row(self, **_kwargs):
        return self._child()

    def column(self, **_kwargs):
        return self._child()

    def label(self, **_kwargs):
        return None

    def separator(self, **_kwargs):
        return None

    def prop(self, _data, name, **_kwargs):
        self.events.append(("prop", name))

    def operator(self, name, **_kwargs):
        self.events.append(("operator", name))
        return SimpleNamespace()


def _clean_scene():
    state = getattr(bpy.context.window_manager, "remi_session", None)
    if state is not None and state.active:
        session.runtime.cancel(bpy.context)
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _cube(name):
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.name = name + "Mesh"
    return obj


def _triangulated_candidate(obj):
    candidate = obj.copy()
    candidate.data = obj.data.copy()
    bpy.context.collection.objects.link(candidate)
    bm = bmesh.new()
    bm.from_mesh(candidate.data)
    bmesh.ops.triangulate(bm, faces=list(bm.faces))
    bm.to_mesh(candidate.data)
    bm.free()
    candidate.data.update()
    return candidate


class _TriangulateStage:
    next_stage = "REPAIR"
    requires_source_checkpoint = False

    def __init__(self, label="Test Step", mutate=None):
        self.label = label
        self.mutate = mutate

    def build(
        self,
        _context,
        _source,
        working_copy,
        source_checkpoint=None,
        disk=None,
    ):
        bm = bmesh.new()
        bm.from_mesh(working_copy.data)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bm.to_mesh(working_copy.data)
        bm.free()
        working_copy.data.update()
        if self.mutate:
            self.mutate(working_copy)
        return working_copy, "", {}


def _open_top(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    top = max(bm.faces, key=lambda face: face.calc_center_median().z)
    bm.faces.remove(top)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _assert_locked(context, expected):
    other = _cube("Unrelated")
    bpy.ops.object.select_all(action="DESELECT")
    other.select_set(True)
    context.view_layer.objects.active = other
    assert session.runtime.ensure_active_object(context)
    current = session.runtime.object(context)
    assert current is expected
    assert context.view_layer.objects.active is expected
    assert expected.select_get()
    assert not other.select_get()
    bpy.data.objects.remove(other, do_unlink=True)


def test_disk_service_reclaims_abandoned_sessions():
    with tempfile.TemporaryDirectory(prefix="remi-disk-service-test-") as root:
        root_path = Path(root).resolve()
        live = SessionDiskService.create("live-session", root=root)
        dead = SessionDiskService.create("dead-session", root=root)
        live_directory = live.directory
        dead_directory = dead.directory

        dead_owner = dead_directory / SessionDiskService.OWNER_FILENAME
        owner = json.loads(dead_owner.read_text(encoding="utf-8"))
        owner["pid"] = 2_147_483_647
        dead_owner.write_text(json.dumps(owner), encoding="utf-8")

        legacy_directory = root_path / "remi-session-legacy"
        legacy_directory.mkdir()
        old_time = time.time() - SessionDiskService.STARTUP_GRACE_SECONDS - 10.0
        os.utime(legacy_directory, (old_time, old_time))
        fresh_directory = root_path / "remi-session-starting"
        fresh_directory.mkdir()
        unrelated_directory = root_path / "another-addon-cache"
        unrelated_directory.mkdir()

        removed = set(
            SessionDiskService.cleanup_abandoned(
                root=root,
                current_pid=-1,
            )
        )
        assert str(dead_directory) in removed
        assert str(legacy_directory) in removed
        assert live_directory.is_dir()
        assert fresh_directory.is_dir()
        assert unrelated_directory.is_dir()

        workspace = live.create_workspace("mesh export")
        (workspace / "intermediate.ply").write_text("fixture", encoding="utf-8")
        assert workspace.parent == live_directory
        assert live.release_workspace(workspace)
        assert not workspace.exists()

        reload_orphan = SessionDiskService.create("reload-session", root=root)
        reload_directory = reload_orphan.directory
        removed = set(
            SessionDiskService.cleanup_abandoned(
                root=root,
                preserve=[live_directory],
            )
        )
        assert str(reload_directory) in removed
        assert fresh_directory.is_dir()

        assert live.close()
        assert not live_directory.exists()
    print("PASS disk service preserves live owners and reclaims crash leftovers")


def test_history_and_finish():
    _clean_scene()
    source = _cube("LockedScan")
    source["asset_note"] = "preserve me"
    source.modifiers.new("OriginalModifier", "TRIANGULATE")
    material = bpy.data.materials.new("HistoryMaterial")
    material.use_nodes = True
    image = bpy.data.images.new("HistoryImage", width=8, height=8)
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    source.data.materials.append(material)
    original_faces = len(source.data.polygons)

    session.runtime.begin(bpy.context, source)
    state = bpy.context.window_manager.remi_session
    cache_dir = session.runtime.disk.directory
    assert state.active
    assert session.runtime.disk.exists("source")
    assert len(bpy.context.scene.objects) == 1
    _assert_locked(bpy.context, source)

    session.runtime.execute_action(bpy.context, action_for_stage(_TriangulateStage()))
    current = session.runtime.object(bpy.context)
    assert current.name == "LockedScan"
    assert len(bpy.context.scene.objects) == 1
    assert len(current.data.polygons) == 12
    assert state.can_undo and not state.can_redo

    session.runtime.undo(bpy.context)
    current = session.runtime.object(bpy.context)
    assert len(current.data.polygons) == original_faces
    assert not state.can_undo and state.can_redo
    assert len(bpy.context.scene.objects) == 1

    session.runtime.redo(bpy.context)
    current = session.runtime.object(bpy.context)
    assert len(current.data.polygons) == 12
    assert state.can_undo and not state.can_redo

    session.runtime.reset(bpy.context)
    current = session.runtime.object(bpy.context)
    assert len(current.data.polygons) == original_faces
    assert current["asset_note"] == "preserve me"
    assert [modifier.name for modifier in current.modifiers] == ["OriginalModifier"]
    assert state.can_redo

    session.runtime.redo(bpy.context)
    current = session.runtime.finish(bpy.context)
    assert len(current.data.polygons) == 12
    assert session.SESSION_ID_KEY not in current
    assert not state.active
    assert not cache_dir.exists()
    assert len(bpy.context.scene.objects) == 1
    assert [item.name for item in bpy.data.materials if item.name.startswith("HistoryMaterial")] == [
        "HistoryMaterial"
    ]
    assert [item.name for item in bpy.data.images if item.name.startswith("HistoryImage")] == [
        "HistoryImage"
    ]
    print("PASS disk-backed Back, Redo, Reset, and Finish")


def test_cancel_restores_exact_source():
    _clean_scene()
    source = _cube("CancelScan")
    source["asset_note"] = "original"
    material = bpy.data.materials.new("OriginalMaterial")
    source.data.materials.append(material)
    source.modifiers.new("OriginalModifier", "TRIANGULATE")
    original_faces = len(source.data.polygons)

    session.runtime.begin(bpy.context, source)
    cache_dir = session.runtime.disk.directory
    def mutate(candidate):
        candidate["asset_note"] = "changed"
        candidate.modifiers.clear()
        candidate.data.materials.clear()

    session.runtime.execute_action(
        bpy.context,
        action_for_stage(_TriangulateStage("Destructive Test", mutate=mutate)),
    )

    restored = session.runtime.cancel(bpy.context)
    assert restored.name == "CancelScan"
    assert restored["asset_note"] == "original"
    assert len(restored.data.polygons) == original_faces
    assert [modifier.name for modifier in restored.modifiers] == ["OriginalModifier"]
    assert restored.data.materials[0].name.startswith("OriginalMaterial")
    assert session.SESSION_ID_KEY not in restored
    assert not cache_dir.exists()
    assert len(bpy.context.scene.objects) == 1
    print("PASS Cancel restores the exact source object")


def test_parented_mesh_checkpoint_selects_the_mesh():
    _clean_scene()
    parent = bpy.data.objects.new("world", None)
    bpy.context.collection.objects.link(parent)
    source = _cube("geometry_0")
    source.parent = parent
    original_faces = len(source.data.polygons)

    session.runtime.begin(bpy.context, source)
    with bpy.data.libraries.load(session.runtime.disk.path("source"), link=False) as (data_from, _data_to):
        assert set(data_from.objects) == {"world", "geometry_0"}

    session.runtime.execute_action(
        bpy.context,
        action_for_stage(_TriangulateStage("Parented Test")),
    )
    assert len(session.runtime.object(bpy.context).data.polygons) > original_faces
    session.runtime.undo(bpy.context)
    restored = session.runtime.object(bpy.context)
    assert restored.type == "MESH"
    assert len(restored.data.polygons) == original_faces
    assert restored.parent is parent
    assert [obj.name for obj in bpy.data.objects if obj.name.startswith("world")] == ["world"]
    session.runtime.cancel(bpy.context)
    print("PASS parent dependencies cannot replace the recovery mesh")


def test_failed_stage_cannot_mutate_the_locked_mesh():
    _clean_scene()
    source = _cube("FailureIsolation")
    source["asset_note"] = "locked"
    source_faces = len(source.data.polygons)
    source_mesh = source.data

    class FailingStage:
        label = "Failing Stage"
        next_stage = "REMESH"
        requires_source_checkpoint = False

        @staticmethod
        def build(
            _context,
            _source,
            working_copy,
            source_checkpoint=None,
            disk=None,
        ):
            bm = bmesh.new()
            bm.from_mesh(working_copy.data)
            bmesh.ops.triangulate(bm, faces=list(bm.faces))
            bm.to_mesh(working_copy.data)
            bm.free()
            working_copy["asset_note"] = "mutated copy"
            raise RuntimeError("intentional stage failure")

    session.runtime.begin(bpy.context, source)
    state = bpy.context.window_manager.remi_session
    try:
        session.runtime.execute_action(bpy.context, action_for_stage(FailingStage()))
    except RuntimeError as exc:
        assert str(exc) == "intentional stage failure"
    else:
        raise AssertionError("The failing stage unexpectedly committed")

    current = session.runtime.object(bpy.context)
    assert current is source
    assert current.data is source_mesh
    assert len(current.data.polygons) == source_faces
    assert current["asset_note"] == "locked"
    assert len(bpy.context.scene.objects) == 1
    assert state.current_step == "Source"
    assert not state.can_undo and not state.can_redo and not state.busy
    assert not session.runtime.disk.exists("pending")
    assert not Path(session.runtime.disk.path("pending")).with_suffix(".json").exists()

    session.runtime.execute_action(
        bpy.context,
        action_for_stage(_TriangulateStage("Recovery Test")),
    )
    assert len(session.runtime.object(bpy.context).data.polygons) > source_faces
    session.runtime.undo(bpy.context)
    assert len(session.runtime.object(bpy.context).data.polygons) == source_faces
    session.runtime.cancel(bpy.context)
    print("PASS failed stages cannot mutate the locked mesh or history")


def test_queued_work_is_visible_before_execution():
    _clean_scene()
    source = _cube("QueuedFeedback")
    session.runtime.begin(bpy.context, source)
    state = bpy.context.window_manager.remi_session

    session.runtime.queue(bpy.context, "UV")
    assert state.busy
    assert "Generating UVs" in state.status
    assert "faces" in state.status
    assert session.runtime.pop_command() == "UV"
    state.busy = False

    session.runtime.queue(bpy.context, "BAKE_DIFFUSE")
    assert state.busy
    assert "Bake Albedo" in state.status
    assert "Cycles" in state.status
    assert session.runtime.pop_command() == "BAKE_DIFFUSE"
    state.busy = False
    session.runtime.cancel(bpy.context)
    print("PASS queued UV and Bake work is visible before execution")


def test_real_repair_and_remesh_steps():
    _clean_scene()
    source = _cube("RepairSession")
    _open_top(source)
    settings = bpy.context.scene.remi_settings
    settings.hole_repair_method = "BOUNDARY"
    settings.hole_max_sides = 64
    settings.hole_weld_distance = 0.0

    session.runtime.begin(bpy.context, source)
    _result, repair_report = session.runtime.execute_action(
        bpy.context,
        action_for_command("REPAIR"),
    )
    repaired = session.runtime.object(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert len(repaired.data.polygons) > 5, (
        len(repaired.data.polygons),
        repair_report,
    )
    session.runtime.undo(bpy.context)
    assert len(session.runtime.object(bpy.context).data.polygons) == 5
    session.runtime.cancel(bpy.context)

    source = bpy.context.active_object
    settings.remesh_backend = "VOXEL"
    settings.use_hole_repair = False
    settings.voxel_size = 0.4
    session.runtime.begin(bpy.context, source)
    session.runtime.execute_action(
        bpy.context,
        action_for_command("REMESH"),
    )
    remeshed = session.runtime.object(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert len(remeshed.data.polygons) > 0
    assert len(remeshed.modifiers) == 0
    session.runtime.cancel(bpy.context)

    source = bpy.context.active_object
    for layer in list(source.data.uv_layers):
        source.data.uv_layers.remove(layer)
    session.runtime.begin(bpy.context, source)
    _result, report = session.runtime.execute_action(
        bpy.context,
        action_for_command("UV"),
    )
    assert report["stats"] and report["stats"].valid
    assert len(bpy.context.scene.objects) == 1
    assert session.runtime.object(bpy.context).data.uv_layers.active is not None
    session.runtime.undo(bpy.context)
    assert session.runtime.object(bpy.context).data.uv_layers.active is None
    session.runtime.cancel(bpy.context)
    print("PASS transactional Repair, Remesh, and UV keep one scene object")


def test_manual_hole_repair_is_a_session_step():
    _clean_scene()
    source = _cube("ManualRepairSession")
    original_faces = len(source.data.polygons)
    settings = bpy.context.scene.remi_settings
    settings.voxel_size = 0.25
    settings.alpha_wrap_patch_resolution = 4.0
    settings.alpha_wrap_patch_relax_iterations = 0
    ring = [
        Vector((-0.5, -0.5, 1.0)),
        Vector((0.5, -0.5, 1.0)),
        Vector((0.5, 0.5, 1.0)),
        Vector((-0.5, 0.5, 1.0)),
    ]
    normals = [Vector((0.0, 0.0, 1.0)) for _point in ring]

    session.runtime.begin(bpy.context, source)
    state = bpy.context.window_manager.remi_session
    state.interactive = True
    result, error, report = operators._commit_surface_ring_patch(
        bpy.context,
        source,
        settings,
        ring,
        ring_normals=normals,
    )
    state.interactive = False

    assert not error, error
    assert result is session.runtime.object(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert len(result.data.polygons) >= original_faces + report["patch_faces"]
    assert state.current_step == "Manual Repair"
    assert state.stage == "REPAIR"
    assert state.can_undo

    session.runtime.undo(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert len(session.runtime.object(bpy.context).data.polygons) == original_faces
    session.runtime.redo(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert len(session.runtime.object(bpy.context).data.polygons) > original_faces
    session.runtime.cancel(bpy.context)
    print("PASS manual hole repair commits one mesh with Back and Redo")


def test_repair_ui_keeps_manual_and_advanced_controls():
    settings = bpy.context.scene.remi_settings
    state = SimpleNamespace(interactive=False)

    settings.hole_repair_method = "ALPHA_WRAP"
    alpha_layout = _UILayoutRecorder()
    draw_feature("REPAIR", alpha_layout, state)
    alpha_events = set(alpha_layout.events)
    assert ("operator", "remi.draw_hole_patch") in alpha_events
    assert ("operator", "remi.build_alpha_wrap") in alpha_events
    assert {
        ("prop", "targeted_ray_spacing"),
        ("prop", "targeted_ray_depth_ratio"),
        ("prop", "alpha_wrap_patch_resolution"),
        ("prop", "alpha_wrap_patch_relax_iterations"),
        ("prop", "alpha_wrap_offset_ratio"),
        ("prop", "alpha_wrap_executable"),
        ("prop", "alpha_wrap_auto_build"),
    }.issubset(alpha_events)

    settings.hole_repair_method = "HYBRID"
    settings.hole_detail_recovery = True
    hybrid_layout = _UILayoutRecorder()
    draw_feature("REPAIR", hybrid_layout, state)
    assert ("prop", "hole_detail_ratio") in set(hybrid_layout.events)

    settings.hole_repair_method = "VOLUME"
    volume_layout = _UILayoutRecorder()
    draw_feature("REPAIR", volume_layout, state)
    assert ("prop", "volume_surface_fit_ratio") in set(volume_layout.events)

    settings.remesh_backend = "VOLUME"
    settings.volume_preserve_features = True
    remesh_layout = _UILayoutRecorder()
    draw_feature("REMESH", remesh_layout, state)
    assert {
        ("prop", "volume_surface_fit_ratio"),
        ("prop", "volume_feature_angle"),
        ("prop", "volume_feature_reach"),
    }.issubset(set(remesh_layout.events))
    print("PASS Repair UI retains manual patching and advanced controls")


def test_transactional_bake_uses_source_checkpoint():
    _clean_scene()
    source = _cube("BakeSession")
    material = bpy.data.materials.new("BakeSourceMaterial")
    material.diffuse_color = (0.8, 0.15, 0.05, 1.0)
    material.use_nodes = True
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.8,
        0.15,
        0.05,
        1.0,
    )
    source.data.materials.append(material)

    settings = bpy.context.scene.remi_settings
    uv_result = operators.ensure_remi_uv(
        source,
        profile_id="BALANCED",
        texture_size=256,
        margin_px=4,
        preserve_existing_seams=False,
        replace_existing=True,
    )
    assert uv_result.success, uv_result.error
    settings.bake_texture_size = 256
    settings.bake_auto_unwrap = False
    settings.bake_recalc_normals = True
    settings.bake_half_scale = False
    settings.bake_cage_extrusion = 0.05
    settings.bake_max_ray_distance = 0.1

    session.runtime.begin(bpy.context, source)
    original_ensure_uv = baking.ensure_remi_uv
    try:
        def unexpected_uv_regeneration(*_args, **_kwargs):
            raise AssertionError("Bake repeated UV generation despite an existing UV layer")

        baking.ensure_remi_uv = unexpected_uv_regeneration
        _result, report = session.runtime.execute_action(
            bpy.context,
            action_for_command("BAKE_DIFFUSE"),
        )
    finally:
        baking.ensure_remi_uv = original_ensure_uv
    assert report["success"]

    baked = session.runtime.object(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert baked.data.materials[0].name.startswith("BakeSession_baked")
    assert bpy.data.images.get("BakeSession_diffuse") is not None

    session.runtime.undo(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert bpy.data.materials.get("BakeSession_baked") is None
    assert bpy.data.images.get("BakeSession_diffuse") is None

    session.runtime.redo(bpy.context)
    assert session.runtime.object(bpy.context).data.materials[0].name.startswith("BakeSession_baked")
    assert bpy.data.images.get("BakeSession_diffuse") is not None
    session.runtime.cancel(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert bpy.data.materials.get("BakeSession_baked") is None
    assert bpy.data.images.get("BakeSession_diffuse") is None
    print("PASS transactional Bake uses the source checkpoint and cleans history data")


def test_transactional_decimation_when_available():
    if not operators.mlw.ensure_pymeshlab():
        print("SKIP transactional Decimate: PyMeshLab is not installed")
        return
    _clean_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.0)
    source = bpy.context.active_object
    source.name = "DecimateSession"
    original_faces = len(source.data.polygons)
    settings = bpy.context.scene.remi_settings
    settings.decimation_passes = 1
    settings.target_percentage = 0.5
    settings.decimation_preserve_detail = False
    settings.decimation_with_texture = False

    session.runtime.begin(bpy.context, source)
    session.runtime.execute_action(bpy.context, action_for_command("DECIMATE"))
    assert len(bpy.context.scene.objects) == 1
    assert len(session.runtime.object(bpy.context).data.polygons) < original_faces

    session.runtime.undo(bpy.context)
    assert len(bpy.context.scene.objects) == 1
    assert len(session.runtime.object(bpy.context).data.polygons) == original_faces
    session.runtime.cancel(bpy.context)
    print("PASS transactional MeshLab decimation keeps one scene object")


def test_autoremesher_candidate_commits_in_place():
    _clean_scene()
    source = _cube("AutoRetopoSession")
    original_faces = len(source.data.polygons)
    settings = bpy.context.scene.remi_settings

    originals = {
        "resolve": autoremesher_service.autoremesher.resolve_executable,
        "validate": autoremesher_service.autoremesher.validate_executable,
        "export": mesh_exchange.export_obj_for_tool,
        "run": autoremesher_service.subprocess.run,
        "import": mesh_exchange.import_obj_result,
    }

    def fake_export(_obj, path, export_materials=False):
        Path(path).write_text("fixture", encoding="utf-8")
        return True

    def fake_run(command, **_kwargs):
        output_path = command[command.index("-o") + 1]
        Path(output_path).write_text("fixture", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_import(_path):
        candidate = _triangulated_candidate(source)
        candidate.name = source.name + "_autoremesh_fixture"
        return candidate

    try:
        autoremesher_service.autoremesher.resolve_executable = (
            lambda _configured: Path("/usr/bin/true")
        )
        autoremesher_service.autoremesher.validate_executable = lambda _executable: ""
        mesh_exchange.export_obj_for_tool = fake_export
        autoremesher_service.subprocess.run = fake_run
        mesh_exchange.import_obj_result = fake_import

        session.runtime.begin(bpy.context, source)
        session.runtime.execute_action(bpy.context, action_for_command("AUTO_RETOPO"))
        assert len(bpy.context.scene.objects) == 1
        assert len(session.runtime.object(bpy.context).data.polygons) > original_faces
        session.runtime.undo(bpy.context)
        assert len(bpy.context.scene.objects) == 1
        assert len(session.runtime.object(bpy.context).data.polygons) == original_faces
        session.runtime.cancel(bpy.context)
    finally:
        autoremesher_service.autoremesher.resolve_executable = originals["resolve"]
        autoremesher_service.autoremesher.validate_executable = originals["validate"]
        mesh_exchange.export_obj_for_tool = originals["export"]
        autoremesher_service.subprocess.run = originals["run"]
        mesh_exchange.import_obj_result = originals["import"]
    print("PASS AutoRemesher candidate commits to the locked object")


remi.register()
try:
    test_disk_service_reclaims_abandoned_sessions()
    test_history_and_finish()
    test_cancel_restores_exact_source()
    test_parented_mesh_checkpoint_selects_the_mesh()
    test_failed_stage_cannot_mutate_the_locked_mesh()
    test_queued_work_is_visible_before_execution()
    test_real_repair_and_remesh_steps()
    test_manual_hole_repair_is_a_session_step()
    test_repair_ui_keeps_manual_and_advanced_controls()
    test_transactional_bake_uses_source_checkpoint()
    test_transactional_decimation_when_available()
    test_autoremesher_candidate_commits_in_place()
finally:
    _clean_scene()
    remi.unregister()

print("REMI_SESSION_REGRESSION_OK")
