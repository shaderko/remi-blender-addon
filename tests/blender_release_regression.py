"""Headless release-safety regressions for Remi 1.14.

Run with:
  blender --background --factory-startup --python tests/blender_release_regression.py
"""

from pathlib import Path
from types import SimpleNamespace
import sys

import bmesh
import bpy
from mathutils import Vector


ADDON_PARENT = Path(__file__).resolve().parents[2]
if str(ADDON_PARENT) not in sys.path:
    sys.path.insert(0, str(ADDON_PARENT))

import remi
from remi.features.remesh import geometry_nodes
from remi.features.repair.manual import create_surface_ring_patch
from remi.integrations import meshlab
from remi.blender.mesh_objects import duplicate_object


def _clean_scene():
    if bpy.context.mode == "EDIT_MESH":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _object_from_bmesh(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def _cube(name, open_top=False):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    if open_top:
        top = max(bm.faces, key=lambda face: face.calc_center_median().z)
        bm.faces.remove(top)
    return _object_from_bmesh(name, bm)


def _source_state(obj):
    return {
        "data": obj.data.as_pointer(),
        "vertices": len(obj.data.vertices),
        "edges": len(obj.data.edges),
        "faces": len(obj.data.polygons),
        "modifiers": tuple(modifier.name for modifier in obj.modifiers),
    }


def test_modifier_input_compatibility():
    class LegacyModifier(dict):
        pass

    legacy = LegacyModifier()
    assert geometry_nodes._set_modifier_input(legacy, "Socket_7", 0.125)
    assert legacy["Socket_7"] == 0.125

    modern_value = SimpleNamespace(value=None)
    modern = SimpleNamespace(
        properties=SimpleNamespace(
            inputs=SimpleNamespace(Socket_7=modern_value),
        )
    )
    assert geometry_nodes._set_modifier_input(modern, "Socket_7", 0.25)
    assert modern_value.value == 0.25

    _clean_scene()
    obj = _cube("ModifierCompatibility")
    modifier = geometry_nodes.apply_remi_modifier(obj, voxel_size=0.125)
    node_group = modifier.node_group
    voxel_identifier = next(
        item.identifier
        for item in node_group.interface.items_tree
        if getattr(item, "name", "") == "Voxel Size"
    )
    if bpy.app.version < (5, 2, 0):
        assert abs(float(modifier[voxel_identifier]) - 0.125) < 1e-8
    else:
        value = getattr(modifier.properties.inputs, voxel_identifier).value
        assert abs(float(value) - 0.125) < 1e-8
    print("PASS Blender 5.1/5.2 Geometry Nodes inputs")


def test_duplicate_isolation():
    _clean_scene()
    source = _cube("OnlyThisObject")
    other = _cube("DoNotDuplicate")
    source.select_set(True)
    other.select_set(True)
    before = len(bpy.context.scene.objects)
    duplicate = duplicate_object(source, "_copy")
    assert len(bpy.context.scene.objects) == before + 1
    assert duplicate.name.startswith("OnlyThisObject_copy")
    assert not source.select_get()
    assert not other.select_get()
    print("PASS single-object duplicate isolation")


def test_targeted_patch_preserves_source():
    _clean_scene()
    source = _cube("TargetedSource")
    source.modifiers.new(name="SourceModifier", type="TRIANGULATE")
    before = _source_state(source)

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
    result, error, report = create_surface_ring_patch(
        source,
        settings,
        ring,
        ring_normals=normals,
    )
    assert not error
    assert result is not source
    assert result.name.startswith("TargetedSource_targeted_patch")
    assert _source_state(source) == before
    assert len(result.data.polygons) >= before["faces"] + report["patch_faces"]
    assert len(result.modifiers) == 0
    assert bpy.context.view_layer.objects.active is result
    print("PASS non-destructive targeted hole patch")


def test_boundary_repair_preserves_source():
    _clean_scene()
    source = _cube("BoundarySource", open_top=True)
    source.modifiers.new(name="SourceModifier", type="TRIANGULATE")
    before = _source_state(source)

    settings = bpy.context.scene.remi_settings
    settings.use_hole_repair = True
    settings.hole_repair_method = "BOUNDARY"
    settings.hole_max_sides = 64
    settings.hole_weld_distance = 0.0
    assert bpy.ops.remi.repair_holes() == {"FINISHED"}

    result = bpy.context.view_layer.objects.active
    assert result is not source
    assert result.name.startswith("BoundarySource_prepared")
    assert _source_state(source) == before
    assert len(result.data.polygons) > before["faces"]
    assert len(result.modifiers) == 0
    print("PASS non-destructive boundary repair")


def test_hybrid_repair_preserves_source():
    _clean_scene()
    source = _cube("HybridSource", open_top=True)
    source.modifiers.new(name="SourceModifier", type="TRIANGULATE")
    before = _source_state(source)

    settings = bpy.context.scene.remi_settings
    settings.use_hole_repair = True
    settings.hole_repair_method = "HYBRID"
    settings.hole_max_sides = 64
    settings.hole_weld_distance = 0.0
    settings.voxel_size = 0.25
    settings.hole_close_ratio = 0.05
    settings.hole_detail_recovery = False
    assert bpy.ops.remi.repair_holes() == {"FINISHED"}

    result = bpy.context.view_layer.objects.active
    assert result is not source
    assert result.name.startswith("HybridSource_prepared")
    assert _source_state(source) == before
    assert len(result.data.polygons) > 0
    assert len(result.modifiers) == 0
    print("PASS non-destructive hybrid repair")


def test_full_pipeline_dependency_preflight():
    _clean_scene()
    source = _cube("PreflightSource")
    before = _source_state(source)
    before_objects = tuple(obj.as_pointer() for obj in bpy.context.scene.objects)
    settings = bpy.context.scene.remi_settings
    settings.use_sdf_remesh = True
    settings.use_decimation = True
    settings.use_autoremesher = False
    settings.use_baking = False
    original_check = meshlab.ensure_pymeshlab
    try:
        meshlab.ensure_pymeshlab = lambda: False
        try:
            outcome = bpy.ops.remi.full_pipeline()
        except RuntimeError as exc:
            assert "PyMeshLab is required for decimation" in str(exc)
        else:
            assert outcome == {"CANCELLED"}
    finally:
        meshlab.ensure_pymeshlab = original_check
    assert _source_state(source) == before
    assert tuple(obj.as_pointer() for obj in bpy.context.scene.objects) == before_objects
    print("PASS dependency preflight runs before mesh changes")


def test_standalone_decimation_preserves_source():
    if not meshlab.ensure_pymeshlab():
        print("SKIP standalone decimation: PyMeshLab is not installed")
        return

    _clean_scene()
    source = _cube("DecimationSource")
    before = _source_state(source)
    settings = bpy.context.scene.remi_settings
    settings.decimation_passes = 1
    settings.target_percentage = 0.5
    settings.decimation_preserve_detail = False
    settings.decimation_with_texture = False
    settings.output_name_suffix = "_optimized"
    assert bpy.ops.remi.decimate() == {"FINISHED"}

    result = bpy.context.view_layer.objects.active
    assert result is not source
    assert result.name.startswith("DecimationSource_optimized")
    assert _source_state(source) == before
    assert len(result.data.polygons) > 0
    print("PASS standalone decimation source preservation")


def test_dependency_message_is_explicit():
    command = meshlab.pymeshlab_install_command()
    message = meshlab.pymeshlab_unavailable_message()
    assert sys.executable in command
    assert "pip install" in command
    assert command in message
    assert "auto-install" not in message
    print("PASS explicit PyMeshLab dependency guidance")


remi.register()
try:
    test_modifier_input_compatibility()
    test_duplicate_isolation()
    test_targeted_patch_preserves_source()
    test_boundary_repair_preserves_source()
    test_hybrid_repair_preserves_source()
    test_full_pipeline_dependency_preflight()
    test_standalone_decimation_preserves_source()
    test_dependency_message_is_explicit()
finally:
    _clean_scene()
    remi.unregister()

print("REMI_RELEASE_REGRESSION_OK")
