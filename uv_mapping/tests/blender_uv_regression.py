"""Headless Blender regression coverage for Remi UV.

Run with:
  blender --background --factory-startup --python uv_mapping/tests/blender_uv_regression.py
"""

from pathlib import Path
import math
import sys

import bpy


ADDON_PARENT = Path(__file__).resolve().parents[3]
if str(ADDON_PARENT) not in sys.path:
    sys.path.insert(0, str(ADDON_PARENT))

import remi
from remi.uv_mapping import ensure_remi_uv
from remi.uv_mapping.analysis import analyze_mesh
from remi.uv_mapping.blender_bridge import _repair_uv_flips, _seams_from_active_uv
from remi.uv_mapping.metrics import find_uv_overlaps
from remi.uv_mapping.metrics import evaluate_uv
from remi.uv_mapping.packing import native_packer_available
from remi.uv_mapping.settings import get_profile


def _clean_scene():
    if bpy.context.mode == "EDIT_MESH":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def _assert_valid(result, name):
    assert result.success, f"{name}: {result.error}"
    assert result.stats is not None, f"{name}: missing UV statistics"
    stats = result.stats
    assert stats.valid, f"{name}: invalid stats {stats.to_dict()}"
    assert stats.chart_count >= 1, f"{name}: no charts"
    assert math.isfinite(stats.conformal_p95), f"{name}: non-finite stretch"
    min_u, min_v, max_u, max_v = stats.uv_bounds
    assert min_u >= -1.0e-5 and min_v >= -1.0e-5, f"{name}: UVs below tile {stats.uv_bounds}"
    assert max_u <= 1.00001 and max_v <= 1.00001, f"{name}: UVs above tile {stats.uv_bounds}"
    assert result.chart_count == bpy.context.active_object.get("remi_uv_chart_count")
    print(
        f"PASS {name}: {result.classification}, {stats.chart_count} charts, "
        f"p95={stats.conformal_p95:.3f}, occupancy={stats.packing_occupancy:.1%}"
    )


def _run_primitive(name, create, profile="BALANCED"):
    _clean_scene()
    create()
    obj = bpy.context.active_object
    obj.name = name
    result = ensure_remi_uv(
        obj,
        profile_id=profile,
        texture_size=1024,
        margin_px=8,
        replace_existing=True,
    )
    _assert_valid(result, name)
    return obj, result


def test_primitives():
    _obj, cube = _run_primitive("cube", lambda: bpy.ops.mesh.primitive_cube_add(), "HARD_SURFACE")
    assert cube.chart_count == 6, f"cube: expected six planar charts, got {cube.chart_count}"

    _obj, cylinder = _run_primitive(
        "cylinder",
        lambda: bpy.ops.mesh.primitive_cylinder_add(vertices=32),
        "BALANCED",
    )
    assert 3 <= cylinder.chart_count <= 12, f"cylinder: unexpected chart count {cylinder.chart_count}"
    assert cylinder.stats.packing_occupancy >= 0.70, "cylinder: packing quality regressed"

    _run_primitive(
        "sphere",
        lambda: bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12),
        "ORGANIC",
    )


def test_native_packer_and_smart_quality_guard():
    assert native_packer_available(), "bundled xatlas extension is unavailable"
    _clean_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)
    obj = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(
        angle_limit=math.radians(66.0),
        margin_method="FRACTION",
        rotate_method="AXIS_ALIGNED",
        island_margin=4.0 / 1024.0,
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=True,
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    analysis = analyze_mesh(obj.data)
    smart_seams = _seams_from_active_uv(obj.data)
    smart_stats = evaluate_uv(obj.data, analysis, smart_seams, check_overlaps=True)

    result = ensure_remi_uv(
        obj,
        profile_id="SCAN",
        texture_size=1024,
        margin_px=4,
        preserve_existing_seams=False,
        replace_existing=True,
    )
    _assert_valid(result, "Smart quality guard")

    def quality(stats):
        return (
            stats.packing_occupancy
            - 0.08 * (stats.chart_count / max(1, stats.triangle_count))
            - 0.02 * min(10.0, max(0.0, stats.conformal_p95 - 1.0))
        )

    if smart_stats.valid:
        assert quality(result.stats) + 1.0e-6 >= quality(smart_stats), (
            "Remi returned a lower-quality atlas than its Smart UV baseline: "
            f"Remi={result.stats.to_dict()}, Smart={smart_stats.to_dict()}"
        )
    print("PASS native xatlas backend and Smart UV quality guard")


def test_face_local_flip_repair():
    _clean_scene()
    mesh = bpy.data.meshes.new("BowTieUvQuad")
    mesh.from_pydata(
        [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
        ],
        [],
        [(0, 1, 2, 3)],
    )
    obj = bpy.data.objects.new("BowTieUvQuad", mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    uv_layer = mesh.uv_layers.new(name="FoldedUV")
    bow_tie = ((0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0))
    for loop, uv in zip(uv_layer.data, bow_tie):
        loop.uv = uv

    analysis = analyze_mesh(mesh)
    initial = evaluate_uv(mesh, analysis, set(), check_overlaps=True)
    assert initial.flipped_triangles == 1
    assert initial.flipped_faces == [0]
    warnings = []
    repaired_stats, repaired_seams, repaired = _repair_uv_flips(
        obj,
        mesh,
        analysis,
        set(),
        get_profile("BALANCED"),
        512,
        4,
        initial,
        set(),
        warnings,
    )
    assert repaired, "face-local flip repair was not accepted"
    assert repaired_stats.valid, repaired_stats.to_dict()
    assert isinstance(repaired_seams, set)
    assert any("flipped UV" in warning for warning in warnings)
    print("PASS face-local flipped UV repair")
    _run_primitive(
        "torus",
        lambda: bpy.ops.mesh.primitive_torus_add(major_segments=24, minor_segments=10),
        "BALANCED",
    )
    _run_primitive(
        "open_grid",
        lambda: bpy.ops.mesh.primitive_grid_add(x_subdivisions=12, y_subdivisions=12),
        "BALANCED",
    )


def test_existing_uv_short_circuit():
    _clean_scene()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    artist_uv = obj.data.uv_layers.active
    assert artist_uv is not None
    artist_uv.name = "ArtistUV"
    result = ensure_remi_uv(obj, replace_existing=False)
    assert result.success and not result.created
    assert obj.data.uv_layers.active.name == "ArtistUV"

    while obj.data.uv_layers:
        obj.data.uv_layers.remove(obj.data.uv_layers[0])
    blank_uv = obj.data.uv_layers.new(name="BlankUV")
    obj.data.uv_layers.active = blank_uv
    regenerated = ensure_remi_uv(obj, replace_existing=False)
    assert regenerated.success and regenerated.created
    assert any("failed validation" in warning for warning in regenerated.warnings)
    print("PASS existing UV validation and preservation")


def test_seam_and_edit_state_preservation():
    _clean_scene()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    obj = bpy.context.active_object
    obj.data.edges[0].use_seam = True
    for vertex in obj.data.vertices:
        vertex.select = False
    for edge in obj.data.edges:
        edge.select = False
    for polygon in obj.data.polygons:
        polygon.select = False
    obj.data.polygons[3].select = True
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bpy.ops.object.mode_set(mode="EDIT")

    result = ensure_remi_uv(
        obj,
        profile_id="ORGANIC",
        texture_size=512,
        margin_px=4,
        preserve_existing_seams=True,
        replace_existing=True,
    )
    assert result.success, result.error
    assert bpy.context.mode == "EDIT_MESH", "edit mode was not restored"
    bpy.ops.object.mode_set(mode="OBJECT")
    assert obj.data.edges[0].use_seam, "artist seam was removed"
    selected = [polygon.index for polygon in obj.data.polygons if polygon.select]
    assert selected == [3], f"face selection was not restored: {selected}"
    print("PASS seam and edit-state preservation")


def test_repeatability():
    _clean_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3)
    obj = bpy.context.active_object
    first = ensure_remi_uv(obj, profile_id="SCAN", replace_existing=True)
    _assert_valid(first, "repeatability-first")
    first_uvs = [tuple(round(value, 6) for value in loop.uv) for loop in obj.data.uv_layers.active.data]
    second = ensure_remi_uv(obj, profile_id="SCAN", replace_existing=True)
    _assert_valid(second, "repeatability-second")
    second_uvs = [tuple(round(value, 6) for value in loop.uv) for loop in obj.data.uv_layers.active.data]
    assert first_uvs == second_uvs, "Remi UV output changed between identical runs"
    print("PASS deterministic repeatability")


def test_topology_edge_cases():
    _clean_scene()
    mesh = bpy.data.meshes.new("NonManifoldMesh")
    mesh.from_pydata(
        [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.2, 1.0, 0.0),
            (0.2, -1.0, 0.0),
            (0.2, 0.0, 1.0),
        ],
        [],
        [(0, 1, 2), (1, 0, 3), (0, 1, 4)],
    )
    obj = bpy.data.objects.new("NonManifold", mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    result = ensure_remi_uv(obj, profile_id="SCAN", replace_existing=True)
    _assert_valid(result, "non-manifold fan")
    assert result.classification == "IRREGULAR"

    _clean_scene()
    bpy.ops.mesh.primitive_cube_add(location=(-2.0, 0.0, 0.0))
    left = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(location=(2.0, 0.0, 0.0))
    right = bpy.context.active_object
    left.select_set(True)
    right.select_set(True)
    bpy.context.view_layer.objects.active = right
    bpy.ops.object.join()
    joined = bpy.context.active_object
    for edge in joined.data.edges:
        edge.use_seam = False
    result = ensure_remi_uv(joined, profile_id="HARD_SURFACE", replace_existing=True)
    _assert_valid(result, "disconnected components")
    assert result.classification == "HARD_SURFACE"
    assert result.chart_count == 12, f"disconnected cubes: expected 12 charts, got {result.chart_count}"
    print("PASS topology edge cases")


def test_registered_operator():
    _clean_scene()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    while obj.data.uv_layers:
        obj.data.uv_layers.remove(obj.data.uv_layers[0])
    settings = bpy.context.scene.remi_settings
    assert settings.bake_uv_method == "REMI"
    assert settings.bake_uv_profile == "NORMAL_BAKE"
    result = bpy.ops.remi.generate_uv()
    assert "FINISHED" in result, f"registered operator failed: {result}"
    assert obj.data.uv_layers.active is not None
    print("PASS registered operator and defaults")


def test_material_boundaries_and_pixel_padding():
    _clean_scene()
    mesh = bpy.data.meshes.new("MaterialPanels")
    mesh.from_pydata(
        [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (1.0, 1.0, 0.0),
            (2.0, 1.0, 0.0),
        ],
        [],
        [(0, 1, 4, 3), (1, 2, 5, 4)],
    )
    mesh.materials.append(bpy.data.materials.new("LeftMaterial"))
    mesh.materials.append(bpy.data.materials.new("RightMaterial"))
    mesh.polygons[0].material_index = 0
    mesh.polygons[1].material_index = 1
    obj = bpy.data.objects.new("MaterialPanels", mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    material_result = ensure_remi_uv(obj, profile_id="BALANCED", replace_existing=True)
    _assert_valid(material_result, "material panels")
    assert material_result.chart_count == 2, "material transition was not used as a chart boundary"

    _clean_scene()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    for edge in obj.data.edges:
        edge.use_seam = False
    low_padding = ensure_remi_uv(
        obj,
        profile_id="HARD_SURFACE",
        texture_size=1024,
        margin_px=4,
        preserve_existing_seams=False,
        replace_existing=True,
    )
    _assert_valid(low_padding, "pixel padding 4")
    high_padding = ensure_remi_uv(
        obj,
        profile_id="HARD_SURFACE",
        texture_size=1024,
        margin_px=32,
        preserve_existing_seams=False,
        replace_existing=True,
    )
    _assert_valid(high_padding, "pixel padding 32")
    assert high_padding.stats.packing_occupancy < low_padding.stats.packing_occupancy
    print("PASS material boundaries and pixel-derived padding")


def test_overlap_pair_diagnostics():
    _clean_scene()
    mesh = bpy.data.meshes.new("OverlappingUvTriangles")
    mesh.from_pydata(
        [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (2.0, 0.0, 0.0),
            (3.0, 0.0, 0.0),
            (2.0, 1.0, 0.0),
        ],
        [],
        [(0, 1, 2), (3, 4, 5)],
    )
    uv_layer = mesh.uv_layers.new(name="InvalidStack")
    triangle_uvs = ((0.1, 0.1), (0.9, 0.1), (0.1, 0.9))
    for loop_index, loop in enumerate(uv_layer.data):
        loop.uv = triangle_uvs[loop_index % 3]
    obj = bpy.data.objects.new("OverlappingUvTriangles", mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    details = find_uv_overlaps(mesh)
    assert len(details) == 1
    assert {details[0]["polygon_a"], details[0]["polygon_b"]} == {0, 1}
    result = ensure_remi_uv(obj, replace_existing=False)
    _assert_valid(result, "overlap diagnostic repair")
    assert not find_uv_overlaps(mesh)
    print("PASS overlap-pair diagnostics and invalid atlas regeneration")


def main():
    remi.register()
    try:
        test_primitives()
        test_native_packer_and_smart_quality_guard()
        test_face_local_flip_repair()
        test_existing_uv_short_circuit()
        test_seam_and_edit_state_preservation()
        test_repeatability()
        test_topology_edge_cases()
        test_registered_operator()
        test_material_boundaries_and_pixel_padding()
        test_overlap_pair_diagnostics()
        print("REMI UV REGRESSION: ALL TESTS PASSED")
    finally:
        if bpy.context.mode == "EDIT_MESH":
            bpy.ops.object.mode_set(mode="OBJECT")
        remi.unregister()


if __name__ == "__main__":
    main()
