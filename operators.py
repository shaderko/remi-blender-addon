"""
Blender operators for the Remi pipeline.
"""

import os
import sys
import json
import math
import select
import subprocess
import tempfile
from pathlib import Path
import bmesh
import bpy
from bpy.types import Operator
from bpy.props import BoolProperty
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from . import gn_setup
from . import meshlab_wrapper as mlw
from . import autoremesher as arm
from . import baking
from . import alpha_wrap as aw


# ============================================================
# Utility helpers
# ============================================================

def _get_temp_dir() -> str:
    """Return a temp directory for intermediate files."""
    temp_dir = os.path.join(tempfile.gettempdir(), "autoremesh")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def _duplicate_object(obj: bpy.types.Object, suffix: str = "_copy") -> bpy.types.Object:
    """Create a duplicate of an object (for processing, keeping original intact)."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.duplicate()
    dup = bpy.context.view_layer.objects.active
    dup.name = obj.name + suffix
    obj.select_set(False)
    return dup


def _export_ply(obj: bpy.types.Object, filepath: str) -> bool:
    """Export a single object as PLY (no axis conversion — PyMeshLab compatible).

    We use PLY instead of OBJ because PyMeshLab applies a Y-up↔Z-up axis
    conversion when reading OBJ files, which silently swaps Y/Z coordinates.
    PLY is a raw vertex format that passes through without transformation.
    """
    prev_active = bpy.context.view_layer.objects.active
    prev_selected = bpy.context.selected_objects.copy()

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    try:
        bpy.ops.wm.ply_export(
            filepath=filepath,
            export_selected_objects=True,
            apply_modifiers=True,
        )
        success = True
    except Exception as e:
        print(f"Remi: PLY export failed: {e}")
        success = False

    bpy.ops.object.select_all(action="DESELECT")
    if prev_active:
        prev_active.select_set(True)
        bpy.context.view_layer.objects.active = prev_active
    for o in prev_selected:
        if o != prev_active:
            o.select_set(True)

    return success


def _export_obj_for_tool(obj: bpy.types.Object, filepath: str) -> bool:
    """Export as OBJ for external tool consumption (AutoRemesher)."""
    prev_active = bpy.context.view_layer.objects.active
    prev_selected = bpy.context.selected_objects.copy()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.wm.obj_export(
            filepath=filepath,
            export_selected_objects=True,
            apply_modifiers=True,
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
            export_materials=False,
        )
        success = True
    except Exception:
        success = False
    bpy.ops.object.select_all(action="DESELECT")
    if prev_active:
        prev_active.select_set(True)
        bpy.context.view_layer.objects.active = prev_active
    for o in prev_selected:
        if o != prev_active:
            o.select_set(True)
    return success


def _import_obj_result(filepath: str) -> bpy.types.Object:
    """Import an OBJ result from an external tool, restoring selection."""
    prev_selected = bpy.context.selected_objects.copy()
    prev_active = bpy.context.view_layer.objects.active
    bpy.ops.wm.obj_import(
        filepath=filepath,
        use_split_objects=False,
        use_split_groups=False,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    imported = [o for o in bpy.context.selected_objects if o not in prev_selected]
    mesh_objs = [o for o in imported if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in prev_selected:
        o.select_set(True)
    if prev_active:
        bpy.context.view_layer.objects.active = prev_active
    return mesh_objs[0] if mesh_objs else None


def _import_ply(filepath: str) -> bpy.types.Object:
    """Import a PLY file and return the first mesh object."""
    prev_selected = bpy.context.selected_objects.copy()
    prev_active = bpy.context.view_layer.objects.active

    bpy.ops.wm.ply_import(filepath=filepath)

    # Find newly imported objects
    imported = [o for o in bpy.context.selected_objects if o not in prev_selected]
    mesh_objs = [o for o in imported if o.type == "MESH"]

    # Restore selection
    bpy.ops.object.select_all(action="DESELECT")
    for o in prev_selected:
        o.select_set(True)
    if prev_active:
        bpy.context.view_layer.objects.active = prev_active

    return mesh_objs[0] if mesh_objs else None


def _apply_modifiers(obj: bpy.types.Object):
    """Apply all modifiers on an object (makes them permanent)."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    for mod in obj.modifiers:
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception as e:
            print(f"Remi: Could not apply modifier '{mod.name}': {e}")


def _local_bounds_diagonal(obj: bpy.types.Object) -> float:
    """Return a scale-independent local-space bounding-box diagonal."""
    if not obj.data.vertices:
        return 0.0
    first = obj.data.vertices[0].co
    min_co = first.copy()
    max_co = first.copy()
    for vertex in obj.data.vertices[1:]:
        coordinate = vertex.co
        min_co.x = min(min_co.x, coordinate.x)
        min_co.y = min(min_co.y, coordinate.y)
        min_co.z = min(min_co.z, coordinate.z)
        max_co.x = max(max_co.x, coordinate.x)
        max_co.y = max(max_co.y, coordinate.y)
        max_co.z = max(max_co.z, coordinate.z)
    return (max_co - min_co).length


def _world_bounds_diagonal(obj: bpy.types.Object) -> float:
    """Return the world-space bounds diagonal used by exported helper meshes."""
    corners = [obj.matrix_world @ type(obj.location)(corner) for corner in obj.bound_box]
    if not corners:
        return 0.0
    min_co = corners[0].copy()
    max_co = corners[0].copy()
    for coordinate in corners[1:]:
        min_co.x = min(min_co.x, coordinate.x)
        min_co.y = min(min_co.y, coordinate.y)
        min_co.z = min(min_co.z, coordinate.z)
        max_co.x = max(max_co.x, coordinate.x)
        max_co.y = max(max_co.y, coordinate.y)
        max_co.z = max(max_co.z, coordinate.z)
    return (max_co - min_co).length


def _repair_boundary_holes(
    obj: bpy.types.Object,
    max_sides: int,
    weld_distance: float = 0.0,
) -> dict:
    """Weld tiny cracks and triangulate bounded boundary loops in-place."""
    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if weld_distance > 0.0 and bm.verts:
            bmesh.ops.remove_doubles(
                bm,
                verts=list(bm.verts),
                dist=float(weld_distance),
            )
        boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
        result = bmesh.ops.holes_fill(
            bm,
            edges=boundary_edges,
            sides=max(3, int(max_sides)),
        ) if boundary_edges else {"faces": []}
        new_faces = list(result.get("faces", []))
        if new_faces:
            bmesh.ops.triangulate(
                bm,
                faces=new_faces,
                quad_method="BEAUTY",
                ngon_method="BEAUTY",
            )
        if bm.faces:
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
        mesh.update()
        remaining = sum(1 for edge in bm.edges if edge.is_boundary)
        return {
            "boundary_edges_before": len(boundary_edges),
            "boundary_edges_after": remaining,
            "new_faces": len(new_faces),
        }
    finally:
        bm.free()


def _hole_close_distance(obj: bpy.types.Object, settings) -> float:
    if not settings.use_hole_repair:
        return 0.0
    if settings.hole_repair_method != "HYBRID":
        return 0.0
    return _local_bounds_diagonal(obj) * float(settings.hole_close_ratio)


def _detail_recovery_distance(obj: bpy.types.Object, settings) -> float:
    if not settings.use_hole_repair or not settings.hole_detail_recovery:
        return 0.0
    if settings.hole_repair_method != "HYBRID":
        return 0.0
    relative_reach = _local_bounds_diagonal(obj) * float(settings.hole_detail_ratio)
    return max(relative_reach, float(settings.voxel_size) * 2.0)


def _prepare_hole_repair(obj: bpy.types.Object, settings) -> dict:
    """Run the topology portion of the optional hybrid pre-repair."""
    if not settings.use_hole_repair:
        return {"boundary_edges_before": 0, "boundary_edges_after": 0, "new_faces": 0}
    if settings.hole_repair_method not in {"HYBRID", "BOUNDARY"}:
        return {"boundary_edges_before": 0, "boundary_edges_after": 0, "new_faces": 0}
    return _repair_boundary_holes(
        obj,
        max_sides=settings.hole_max_sides,
        weld_distance=settings.hole_weld_distance,
    )


def _resolve_alpha_wrap(settings) -> tuple[Path, str]:
    """Find the CGAL helper, optionally building it once on demand."""
    executable = aw.resolve_executable(settings.alpha_wrap_executable)
    error = aw.validate_executable(executable)
    if error and settings.alpha_wrap_auto_build:
        result = aw.build_helper()
        if result.get("success"):
            executable = Path(result["executable"])
            error = aw.validate_executable(executable)
        else:
            error = result.get("error", error)
    if error:
        error = (
            f"{error}. Install CGAL and CMake (macOS: brew install cgal cmake; "
            "Ubuntu: apt install libcgal-dev cmake), then click Build Helper."
        )
    return executable, error


def _create_alpha_wrap_guide(
    source: bpy.types.Object,
    settings,
    suffix: str = "_wrapped",
    alpha_ratio: float = None,
) -> tuple:
    """Run compiled Alpha Wrapping and import its temporary watertight guide."""
    executable, error = _resolve_alpha_wrap(settings)
    if error:
        return None, error, {}

    diagonal = _world_bounds_diagonal(source)
    if diagonal <= 0.0:
        return None, "The source mesh has zero-size bounds", {}
    alpha = diagonal * float(
        settings.alpha_wrap_alpha_ratio if alpha_ratio is None else alpha_ratio
    )
    offset = diagonal * float(settings.alpha_wrap_offset_ratio)
    # CGAL requires offset < alpha. Keep an invalid UI combination safe.
    offset = min(offset, alpha * 0.95)

    try:
        with tempfile.TemporaryDirectory(prefix="remi_alpha_wrap_") as temp_dir:
            input_path = os.path.join(temp_dir, "input.ply")
            output_path = os.path.join(temp_dir, "wrapped.ply")
            if not _export_ply(source, input_path):
                return None, "Could not export the source mesh for Alpha Wrap", {}
            command = aw.build_command(executable, input_path, output_path, alpha, offset)
            process = subprocess.run(
                command,
                cwd=str(executable.parent),
                capture_output=True,
                text=True,
                check=False,
            )
            if process.returncode != 0:
                message = process.stderr.strip() or process.stdout.strip() or "Alpha Wrap failed"
                return None, message, {}
            if not os.path.isfile(output_path):
                return None, "Alpha Wrap completed without producing an output mesh", {}
            wrapped = _import_ply(output_path)
            if not wrapped:
                return None, "Blender could not import the Alpha Wrap result", {}
            wrapped.name = source.name + suffix
            report = {}
            try:
                report = json.loads(process.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                pass
    except OSError as exc:
        return None, str(exc), {}

    bpy.ops.object.select_all(action="DESELECT")
    wrapped.select_set(True)
    bpy.context.view_layer.objects.active = wrapped
    return wrapped, "", report


def _evaluated_world_surface(obj: bpy.types.Object):
    """Build a world-space triangle BVH and sampled open-boundary midpoints."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
        if not vertices or not triangles:
            return None, []
        bvh = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)

        edge_counts = {}
        for polygon in mesh.polygons:
            for edge_key in polygon.edge_keys:
                edge_counts[edge_key] = edge_counts.get(edge_key, 0) + 1
        boundary_points = [
            (vertices[a] + vertices[b]) * 0.5
            for (a, b), count in edge_counts.items()
            if count != 2
        ]
        # Coverage validation does not need every edge on very dense assets.
        if len(boundary_points) > 4096:
            step = len(boundary_points) / 4096.0
            boundary_points = [
                boundary_points[int(index * step)] for index in range(4096)
            ]
        return bvh, boundary_points
    finally:
        evaluated.to_mesh_clear()


def _remove_mesh_object(obj: bpy.types.Object):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _dilate_guide_faces(guide, selected_faces: set, rings: int) -> set:
    """Grow a guide-face mask by topological rings."""
    selected_faces = set(selected_faces)
    guide_mesh = guide.data
    if selected_faces and rings > 0:
        edge_faces = {}
        for polygon in guide_mesh.polygons:
            for edge_key in polygon.edge_keys:
                edge_faces.setdefault(edge_key, []).append(polygon.index)
        frontier = set(selected_faces)
        for _ in range(int(rings)):
            expanded = set(frontier)
            for face_index in frontier:
                for edge_key in guide_mesh.polygons[face_index].edge_keys:
                    expanded.update(edge_faces.get(edge_key, ()))
            frontier = expanded - selected_faces
            selected_faces.update(expanded)
    return selected_faces


def _guide_patch_faces(guide, source_bvh, detection_distance: float, rings: int) -> set:
    """Select guide faces whose centers span empty space, plus an overlap band."""
    selected_faces = set()
    for polygon in guide.data.polygons:
        center_world = guide.matrix_world @ polygon.center
        center_nearest = source_bvh.find_nearest(center_world)
        center_distance = float(center_nearest[3]) if center_nearest else float("inf")
        if center_distance > detection_distance:
            selected_faces.add(polygon.index)
    return _dilate_guide_faces(guide, selected_faces, rings)


def _guide_boundary_coverage(
    guide,
    selected_faces: set,
    boundary_points: list,
    maximum_distance: float,
) -> float:
    """Measure how much of the source's open boundary is touched by guide patches."""
    if not boundary_points:
        return 1.0
    used_vertices = {
        vertex_index
        for face_index in selected_faces
        for vertex_index in guide.data.polygons[face_index].vertices
    }
    if not used_vertices:
        return 0.0
    tree = KDTree(len(used_vertices))
    for slot, vertex_index in enumerate(used_vertices):
        coordinate = guide.matrix_world @ guide.data.vertices[vertex_index].co
        tree.insert(coordinate, slot)
    tree.balance()
    covered = 0
    for point in boundary_points:
        _coordinate, _index, distance = tree.find(point)
        if distance <= maximum_distance:
            covered += 1
    return covered / len(boundary_points)


def _compose_source_with_guide_patches(
    source,
    guide,
    source_bvh,
    selected_faces,
    detection_distance,
    settings,
    suffix,
    report,
    projection_distance=None,
    source_boundary_points=None,
):
    """Copy only selected guide patches onto evaluated source geometry."""
    guide_mesh = guide.data
    guide_face_count = len(guide_mesh.polygons)
    prepared = _duplicate_object(source, suffix)
    _apply_modifiers(prepared)
    inverse_world = prepared.matrix_world.inverted()
    used_vertices = {
        vertex_index
        for face_index in selected_faces
        for vertex_index in guide_mesh.polygons[face_index].vertices
    }
    selected_edge_counts = {}
    for face_index in selected_faces:
        for edge_key in guide_mesh.polygons[face_index].edge_keys:
            selected_edge_counts[edge_key] = selected_edge_counts.get(edge_key, 0) + 1
    patch_border_indices = {
        vertex_index
        for edge_key, count in selected_edge_counts.items() if count == 1
        for vertex_index in edge_key
    }
    nearest_locations = {}
    distances = {}
    for vertex_index in used_vertices:
        world_coordinate = guide.matrix_world @ guide_mesh.vertices[vertex_index].co
        nearest = source_bvh.find_nearest(world_coordinate)
        if nearest and nearest[0] is not None:
            nearest_locations[vertex_index] = nearest[0]
            distances[vertex_index] = float(nearest[3])
        else:
            nearest_locations[vertex_index] = world_coordinate
            distances[vertex_index] = float("inf")

    snap_distance = max(detection_distance, float(projection_distance or 0.0))
    if source_boundary_points and patch_border_indices:
        border_tree = KDTree(len(patch_border_indices))
        for slot, vertex_index in enumerate(patch_border_indices):
            border_tree.insert(nearest_locations[vertex_index], slot)
        border_tree.balance()
        covered = 0
        coverage_distance = max(detection_distance * 2.0, snap_distance * 0.5)
        for point in source_boundary_points:
            _coordinate, _index, distance = border_tree.find(point)
            if distance <= coverage_distance:
                covered += 1
        report["boundary_coverage"] = covered / len(source_boundary_points)

    patch_bm = bmesh.new()
    initial_patch_faces = 0
    refinement_steps = 0
    try:
        patch_vertices = {}
        for vertex_index in used_vertices:
            world_coordinate = guide.matrix_world @ guide_mesh.vertices[vertex_index].co
            if (
                vertex_index in patch_border_indices
                or distances[vertex_index] <= snap_distance
            ):
                world_coordinate = nearest_locations[vertex_index]
            patch_vertices[vertex_index] = patch_bm.verts.new(inverse_world @ world_coordinate)
        patch_bm.verts.ensure_lookup_table()
        for face_index in selected_faces:
            polygon = guide_mesh.polygons[face_index]
            try:
                patch_bm.faces.new([patch_vertices[index] for index in polygon.vertices])
                initial_patch_faces += 1
            except ValueError:
                pass

        target_edge_length = max(
            float(settings.voxel_size) * float(settings.alpha_wrap_patch_resolution),
            1e-7,
        )
        for _ in range(7):
            long_edges = [
                edge for edge in patch_bm.edges
                if edge.calc_length() > target_edge_length * 1.35
            ]
            if not long_edges or len(patch_bm.faces) >= 300000:
                break
            bmesh.ops.subdivide_edges(
                patch_bm,
                edges=long_edges,
                cuts=1,
                use_grid_fill=False,
            )
            refinement_steps += 1

        if patch_bm.faces:
            bmesh.ops.triangulate(
                patch_bm,
                faces=list(patch_bm.faces),
                quad_method="BEAUTY",
                ngon_method="BEAUTY",
            )

        boundary_vertices = {
            vertex
            for edge in patch_bm.edges if edge.is_boundary
            for vertex in edge.verts
        }
        interior_vertices = [
            vertex for vertex in patch_bm.verts if vertex not in boundary_vertices
        ]
        for _ in range(int(settings.alpha_wrap_patch_relax_iterations)):
            if not interior_vertices:
                break
            bmesh.ops.smooth_vert(
                patch_bm,
                verts=interior_vertices,
                factor=0.30,
                use_axis_x=True,
                use_axis_y=True,
                use_axis_z=True,
            )
        if patch_bm.faces:
            bmesh.ops.recalc_face_normals(patch_bm, faces=list(patch_bm.faces))

        patch_face_count = len(patch_bm.faces)
        bm = bmesh.new()
        try:
            bm.from_mesh(prepared.data)
            transferred_vertices = {
                vertex: bm.verts.new(vertex.co) for vertex in patch_bm.verts
            }
            for face in patch_bm.faces:
                try:
                    bm.faces.new([transferred_vertices[vertex] for vertex in face.verts])
                except ValueError:
                    pass
            bm.to_mesh(prepared.data)
            prepared.data.update()
        finally:
            bm.free()
    finally:
        patch_bm.free()

    _remove_mesh_object(guide)
    bpy.ops.object.select_all(action="DESELECT")
    prepared.select_set(True)
    bpy.context.view_layer.objects.active = prepared
    report.update({
        "patch_faces": patch_face_count,
        "guide_faces": report.get("guide_faces", guide_face_count),
        "detection_distance": detection_distance,
        "initial_patch_faces": initial_patch_faces,
        "refinement_steps": refinement_steps,
        "projection_distance": snap_distance,
        "projected_border_vertices": len(patch_border_indices),
    })
    return prepared, "", report


def _alpha_wrap_hole_patches(
    source: bpy.types.Object,
    settings,
    suffix: str = "_prepared",
) -> tuple:
    """Keep the original mesh and add only gap-spanning faces from an Alpha Wrap guide."""
    source_bvh, boundary_points = _evaluated_world_surface(source)
    if source_bvh is None:
        return None, "Could not build a surface index for the original mesh", {}
    if not boundary_points:
        prepared = _duplicate_object(source, suffix)
        _apply_modifiers(prepared)
        bpy.ops.object.select_all(action="DESELECT")
        prepared.select_set(True)
        bpy.context.view_layer.objects.active = prepared
        return prepared, "", {
            "patch_faces": 0,
            "guide_faces": 0,
            "detection_distance": 0.0,
            "boundary_coverage": 1.0,
            "chosen_alpha_ratio": 0.0,
            "guide_attempts": 0,
        }

    diagonal = _world_bounds_diagonal(source)
    offset = diagonal * float(settings.alpha_wrap_offset_ratio)
    detection_distance = max(
        diagonal * float(settings.alpha_wrap_patch_ratio),
        offset * 2.5,
    )

    start_ratio = float(settings.alpha_wrap_alpha_ratio)
    max_ratio = max(start_ratio, float(settings.alpha_wrap_max_ratio))
    target_coverage = float(settings.alpha_wrap_coverage_target)
    candidate_ratio = start_ratio
    guide = None
    selected_faces = set()
    wrap_report = {}
    coverage = 0.0
    attempts = 0

    while True:
        attempts += 1
        candidate, error, candidate_report = _create_alpha_wrap_guide(
            source,
            settings,
            "_alpha_guide",
            alpha_ratio=candidate_ratio,
        )
        if error:
            if guide:
                _remove_mesh_object(guide)
            return None, error, {}
        candidate_faces = _guide_patch_faces(
            candidate,
            source_bvh,
            detection_distance,
            int(settings.alpha_wrap_patch_rings),
        )
        candidate_coverage = _guide_boundary_coverage(
            candidate,
            candidate_faces,
            boundary_points,
            max(detection_distance * 2.0, offset * 8.0),
        )
        if candidate_coverage >= coverage or guide is None:
            if guide:
                _remove_mesh_object(guide)
            guide = candidate
            selected_faces = candidate_faces
            wrap_report = candidate_report
            coverage = candidate_coverage
            chosen_ratio = candidate_ratio
        else:
            _remove_mesh_object(candidate)

        if (
            not settings.alpha_wrap_auto_scale
            or coverage >= target_coverage
            or candidate_ratio >= max_ratio - 1e-9
        ):
            break
        candidate_ratio = min(max_ratio, candidate_ratio * 1.65)

    guide_mesh = guide.data
    guide_face_count = len(guide_mesh.polygons)

    if not selected_faces:
        _remove_mesh_object(guide)
        return None, (
            "No hole-spanning guide faces were detected. Lower Hole Detection "
            "or increase Detail Scale so the guide bridges the openings."
        ), {}
    if settings.alpha_wrap_auto_scale and coverage < min(target_coverage, 0.50):
        failed_scale = chosen_ratio
        _remove_mesh_object(guide)
        return None, (
            f"Hole preparation reached only {coverage:.0%} open-boundary coverage "
            f"at the maximum useful scale ({failed_scale:.3g}). Increase Maximum "
            "Scale or lower Hole Detection; the pipeline was stopped instead of "
            "silently producing an inadequately closed remesh."
        ), {}

    report = {
        "guide_faces": wrap_report.get("faces", guide_face_count),
        "boundary_coverage": coverage,
        "chosen_alpha_ratio": chosen_ratio,
        "guide_attempts": attempts,
    }
    return _compose_source_with_guide_patches(
        source,
        guide,
        source_bvh,
        selected_faces,
        detection_distance,
        settings,
        suffix,
        report,
        source_boundary_points=boundary_points,
    )


def _volume_hole_patches(
    source: bpy.types.Object,
    settings,
    suffix: str = "_prepared",
) -> tuple:
    """Use a fine SDF closing only as a guide, retaining its hole patches."""
    source_bvh, boundary_points = _evaluated_world_surface(source)
    if source_bvh is None:
        return None, "Could not build a surface index for the original mesh", {}
    if not boundary_points:
        prepared = _duplicate_object(source, suffix)
        _apply_modifiers(prepared)
        bpy.ops.object.select_all(action="DESELECT")
        prepared.select_set(True)
        bpy.context.view_layer.objects.active = prepared
        return prepared, "", {
            "guide_method": "VOLUME",
            "patch_faces": 0,
            "guide_faces": 0,
            "boundary_coverage": 1.0,
            "guide_voxel_size": 0.0,
        }

    guide = _duplicate_object(source, "_volume_guide")
    _apply_modifiers(guide)
    guide_voxel_size = max(
        float(settings.voxel_size) * float(settings.volume_guide_voxel_scale),
        0.0001,
    )
    close_distance = (
        _local_bounds_diagonal(guide) * float(settings.hole_close_ratio)
    )
    gn_setup.apply_remi_modifier(
        obj=guide,
        voxel_size=guide_voxel_size,
        hole_close_distance=close_distance,
        detail_recovery_distance=0.0,
    )
    _apply_modifiers(guide)
    if not guide.data.polygons:
        _remove_mesh_object(guide)
        return None, "The volume guide produced no geometry", {}

    diagonal = _world_bounds_diagonal(source)
    detection_distance = max(
        diagonal * float(settings.alpha_wrap_patch_ratio),
        guide_voxel_size * 1.5,
    )
    selected_faces = _guide_patch_faces(
        guide,
        source_bvh,
        detection_distance,
        0,
    )
    boundary_tree = KDTree(len(boundary_points))
    for slot, point in enumerate(boundary_points):
        boundary_tree.insert(point, slot)
    boundary_tree.balance()
    boundary_band = max(
        diagonal * float(settings.hole_close_ratio) * 1.25,
        detection_distance * 3.0,
    )
    boundary_filtered_faces = set()
    for face_index in selected_faces:
        center_world = guide.matrix_world @ guide.data.polygons[face_index].center
        nearest_surface = source_bvh.find_nearest(center_world)
        if (
            nearest_surface
            and nearest_surface[0] is not None
            and boundary_tree.find(nearest_surface[0])[2] <= boundary_band
        ):
            boundary_filtered_faces.add(face_index)
    selected_faces = boundary_filtered_faces
    selected_faces = _dilate_guide_faces(
        guide,
        selected_faces,
        int(settings.alpha_wrap_patch_rings),
    )
    coverage = _guide_boundary_coverage(
        guide,
        selected_faces,
        boundary_points,
        max(detection_distance * 2.0, guide_voxel_size * 4.0),
    )
    if not selected_faces:
        _remove_mesh_object(guide)
        return None, (
            "The volume closed no detectable holes. Increase Crack Size or "
            "lower Hole Detection."
        ), {}

    projection_distance = detection_distance
    if settings.hole_detail_recovery:
        projection_distance = max(
            projection_distance,
            diagonal * float(settings.volume_surface_fit_ratio),
        )
    report = {
        "guide_method": "VOLUME",
        "guide_faces": len(guide.data.polygons),
        "boundary_coverage": coverage,
        "guide_voxel_size": guide_voxel_size,
        "close_distance": close_distance,
    }
    return _compose_source_with_guide_patches(
        source,
        guide,
        source_bvh,
        selected_faces,
        detection_distance,
        settings,
        suffix,
        report,
        projection_distance=projection_distance,
        source_boundary_points=boundary_points,
    )


def _resample_screen_lasso(points, spacing: float):
    """Return uniformly spaced samples around a closed 2D stroke."""
    samples = []
    spacing = max(float(spacing), 1.0)
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        distance = math.sqrt(dx * dx + dy * dy)
        steps = max(1, int(math.ceil(distance / spacing)))
        for step in range(steps):
            factor = step / steps
            samples.append((start[0] + dx * factor, start[1] + dy * factor))
    return samples


def _create_surface_ring_patch(
    source,
    settings,
    ring_world,
    ring_normals=None,
    suffix="_targeted_patch",
):
    """Triangulate and fair a local membrane bounded by ray hits on the source.

    Operates on the source object **in-place** — no duplicate is created.
    If *source* has modifiers they are made permanent first.
    """
    from mathutils.geometry import tessellate_polygon

    _apply_modifiers(source)
    inverse_world = source.matrix_world.inverted()
    minimum_spacing = max(float(settings.voxel_size) * 0.20, 1e-7)
    ring_local = []
    for world_point in ring_world:
        local_point = inverse_world @ world_point
        if not ring_local or (local_point - ring_local[-1]).length >= minimum_spacing:
            ring_local.append(local_point)
    if len(ring_local) > 2 and (ring_local[0] - ring_local[-1]).length < minimum_spacing:
        ring_local.pop()
    if len(ring_local) < 3:
        return None, "Too few distinct surface hits to form a patch", {}

    triangles = tessellate_polygon([ring_local])
    if not triangles:
        return None, "The projected stroke could not be triangulated; draw a simpler loop", {}

    patch_bm = bmesh.new()
    refinement_steps = 0
    try:
        ring_vertices = [patch_bm.verts.new(coordinate) for coordinate in ring_local]
        patch_bm.verts.ensure_lookup_table()

        def ring_index(coordinate):
            if isinstance(coordinate, int):
                return coordinate
            return min(
                range(len(ring_local)),
                key=lambda index: (ring_local[index] - coordinate).length_squared,
            )

        for triangle in triangles:
            indices = [ring_index(coordinate) for coordinate in triangle]
            if len(set(indices)) != 3:
                continue
            try:
                patch_bm.faces.new([ring_vertices[index] for index in indices])
            except ValueError:
                pass
        if not patch_bm.faces:
            return None, "The projected stroke produced no valid patch faces", {}

        initial_faces = len(patch_bm.faces)
        target_edge_length = max(
            float(settings.voxel_size) * float(settings.alpha_wrap_patch_resolution),
            minimum_spacing,
        )
        for _ in range(7):
            long_edges = [
                edge for edge in patch_bm.edges
                if edge.calc_length() > target_edge_length * 1.35
            ]
            if not long_edges or len(patch_bm.faces) >= 150000:
                break
            bmesh.ops.subdivide_edges(
                patch_bm,
                edges=long_edges,
                cuts=1,
                use_grid_fill=False,
            )
            refinement_steps += 1

        if patch_bm.faces:
            bmesh.ops.triangulate(
                patch_bm,
                faces=list(patch_bm.faces),
                quad_method="BEAUTY",
                ngon_method="BEAUTY",
            )

        boundary_vertices = {
            vertex
            for edge in patch_bm.edges if edge.is_boundary
            for vertex in edge.verts
        }
        interior_vertices = [
            vertex for vertex in patch_bm.verts if vertex not in boundary_vertices
        ]
        for _ in range(int(settings.alpha_wrap_patch_relax_iterations)):
            if not interior_vertices:
                break
            bmesh.ops.smooth_vert(
                patch_bm,
                verts=interior_vertices,
                factor=0.25,
                use_axis_x=True,
                use_axis_y=True,
                use_axis_z=True,
            )
        bmesh.ops.recalc_face_normals(patch_bm, faces=list(patch_bm.faces))
        if ring_normals:
            expected_world_normal = ring_normals[0].copy()
            for normal in ring_normals[1:]:
                expected_world_normal += normal
            if expected_world_normal.length_squared > 1e-20:
                expected_world_normal.normalize()
                patch_local_normal = next(iter(patch_bm.faces)).normal
                normal_matrix = source.matrix_world.to_3x3().inverted().transposed()
                patch_world_normal = (normal_matrix @ patch_local_normal).normalized()
                if patch_world_normal.dot(expected_world_normal) < 0.0:
                    bmesh.ops.reverse_faces(patch_bm, faces=list(patch_bm.faces))
        patch_faces = len(patch_bm.faces)

        # Transfer the patch onto the source mesh in-place
        bm = bmesh.new()
        try:
            bm.from_mesh(source.data)
            transferred = {vertex: bm.verts.new(vertex.co) for vertex in patch_bm.verts}
            for face in patch_bm.faces:
                try:
                    bm.faces.new([transferred[vertex] for vertex in face.verts])
                except ValueError:
                    pass
            bm.to_mesh(source.data)
            source.data.update()
        finally:
            bm.free()
    finally:
        patch_bm.free()

    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    return source, "", {
        "ray_hits": len(ring_world),
        "ring_vertices": len(ring_local),
        "initial_patch_faces": initial_faces,
        "patch_faces": patch_faces,
        "refinement_steps": refinement_steps,
    }


def _guided_hole_patches(source, settings, suffix="_prepared"):
    if settings.hole_repair_method == "ALPHA_WRAP":
        return _alpha_wrap_hole_patches(source, settings, suffix)
    if settings.hole_repair_method == "VOLUME":
        return _volume_hole_patches(source, settings, suffix)
    return None, "The selected repair method is not guide-based", {}


def _evaluated_world_sharp_edges(source, angle_degrees: float):
    """Return evaluated world-space segments whose adjacent faces form a crease."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        normal_matrix = evaluated.matrix_world.to_3x3().inverted().transposed()
        normals = [(normal_matrix @ polygon.normal).normalized() for polygon in mesh.polygons]
        edge_faces = {}
        for polygon in mesh.polygons:
            for edge_key in polygon.edge_keys:
                edge_faces.setdefault(edge_key, []).append(polygon.index)
        threshold = math.radians(float(angle_degrees))
        return [
            (vertices[edge_key[0]], vertices[edge_key[1]])
            for edge_key, linked_faces in edge_faces.items()
            if len(linked_faces) == 2
            and normals[linked_faces[0]].angle(normals[linked_faces[1]]) >= threshold
        ]
    finally:
        evaluated.to_mesh_clear()


def _closest_point_on_segment(point, start, end):
    direction = end - start
    length_squared = direction.length_squared
    if length_squared <= 1e-20:
        return start.copy()
    factor = max(0.0, min(1.0, (point - start).dot(direction) / length_squared))
    return start + direction * factor


def _fit_volume_remesh_to_source(source, volume, settings) -> dict:
    """Fit a closing-volume result to source faces and sharp crease segments."""
    source_bvh, _boundary_points = _evaluated_world_surface(source)
    if source_bvh is None:
        return {"surface_projected": 0, "feature_fitted": 0, "sharp_edges": 0}

    diagonal = _world_bounds_diagonal(source)
    surface_reach = diagonal * float(settings.volume_surface_fit_ratio)
    world_scale = max(abs(value) for value in volume.matrix_world.to_scale())
    final_world_voxel = max(float(settings.voxel_size) * world_scale, 1e-7)
    feature_reach = final_world_voxel * float(settings.volume_feature_reach)
    inverse_world = volume.matrix_world.inverted()

    sharp_edges = []
    feature_tree = None
    if settings.volume_preserve_features:
        sharp_edges = _evaluated_world_sharp_edges(source, settings.volume_feature_angle)
        samples = []
        sample_spacing = max(feature_reach * 0.45, final_world_voxel * 0.5)
        for edge_index, (start, end) in enumerate(sharp_edges):
            steps = max(1, min(64, int(math.ceil((end - start).length / sample_spacing))))
            for step in range(steps + 1):
                samples.append((start.lerp(end, step / steps), edge_index))
        if samples:
            feature_tree = KDTree(len(samples))
            for coordinate, edge_index in samples:
                feature_tree.insert(coordinate, edge_index)
            feature_tree.balance()

    surface_projected = 0
    feature_fitted = 0
    for vertex in volume.data.vertices:
        coordinate = volume.matrix_world @ vertex.co
        nearest = source_bvh.find_nearest(coordinate)
        if nearest and nearest[0] is not None and nearest[3] <= surface_reach:
            coordinate = nearest[0]
            surface_projected += 1

        if feature_tree is not None:
            _sample, edge_index, _sample_distance = feature_tree.find(coordinate)
            start, end = sharp_edges[edge_index]
            crease_point = _closest_point_on_segment(coordinate, start, end)
            crease_distance = (coordinate - crease_point).length
            if crease_distance <= feature_reach:
                if crease_distance <= final_world_voxel * 0.75:
                    factor = 1.0
                else:
                    factor = (1.0 - crease_distance / feature_reach) ** 2
                coordinate = coordinate.lerp(crease_point, factor)
                feature_fitted += 1
        vertex.co = inverse_world @ coordinate

    volume.data.update()
    return {
        "surface_projected": surface_projected,
        "feature_fitted": feature_fitted,
        "sharp_edges": len(sharp_edges),
        "surface_reach": surface_reach,
        "feature_reach": feature_reach,
    }


def _closing_volume_remesh(source, settings, suffix="_volume_remesh") -> tuple:
    """Create the slow hole-closing remesh and fit it back to source features."""
    result = _duplicate_object(source, suffix)
    _apply_modifiers(result)
    volume_voxel = max(
        float(settings.voxel_size) * float(settings.volume_guide_voxel_scale),
        0.0001,
    )
    close_distance = _local_bounds_diagonal(result) * float(settings.hole_close_ratio)
    gn_setup.apply_remi_modifier(
        obj=result,
        voxel_size=volume_voxel,
        hole_close_distance=close_distance,
        detail_recovery_distance=0.0,
        fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
        smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
    )
    _apply_modifiers(result)
    if not result.data.polygons:
        _remove_mesh_object(result)
        return None, "Closing Volume produced no geometry", {}
    report = _fit_volume_remesh_to_source(source, result, settings)
    report.update({
        "volume_voxel_size": volume_voxel,
        "close_distance": close_distance,
        "faces": len(result.data.polygons),
    })
    bpy.ops.object.select_all(action="DESELECT")
    result.select_set(True)
    bpy.context.view_layer.objects.active = result
    return result, "", report


# ============================================================
# Operators
# ============================================================


class Remi_OT_DrawHolePatch(Operator):
    """Ray-project a viewport lasso and add a local surface membrane in-place."""

    bl_idname = "remi.draw_hole_patch"
    bl_label = "Draw Around Hole"
    bl_description = "Draw around one visible hole; Remi patches it onto the active mesh in-place"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(
            context.mode == "OBJECT"
            and context.active_object
            and context.active_object.type == "MESH"
            and context.area
            and context.area.type == "VIEW_3D"
        )

    def _viewport_point(self, event):
        x = event.mouse_x - self._window_region.x
        y = event.mouse_y - self._window_region.y
        if 0 <= x < self._window_region.width and 0 <= y < self._window_region.height:
            return (float(x), float(y))
        return None

    def _draw_overlay(self):
        if len(self._points) < 2:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader

        coordinates = list(self._points)
        if len(coordinates) > 2:
            coordinates.append(coordinates[0])
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": coordinates})
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(3.0)
        shader.bind()
        shader.uniform_float("color", (0.2, 0.8, 1.0, 0.95))
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")

    def _cleanup(self, context):
        if getattr(self, "_draw_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handle, "WINDOW")
            self._draw_handle = None
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass

    def invoke(self, context, event):
        self._window_region = next(
            (region for region in context.area.regions if region.type == "WINDOW"),
            None,
        )
        if self._window_region is None:
            self.report({"ERROR"}, "Could not find the 3D viewport region")
            return {"CANCELLED"}
        self._region_3d = context.area.spaces.active.region_3d
        self._source_name = context.active_object.name
        self._points = []
        self._drawing = False
        self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_overlay,
            (),
            "WINDOW",
            "POST_PIXEL",
        )
        context.window.cursor_modal_set("CROSSHAIR")
        context.area.header_text_set(
            "Remi: draw ON the surrounding surface around one hole • release to build • Esc cancels"
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._cleanup(context)
            return {"CANCELLED"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            point = self._viewport_point(event)
            if point is not None:
                self._points = [point]
                self._drawing = True
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._drawing:
            point = self._viewport_point(event)
            if point is not None:
                previous = self._points[-1]
                if (point[0] - previous[0]) ** 2 + (point[1] - previous[1]) ** 2 >= 9.0:
                    self._points.append(point)
                    context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self._drawing:
            self._drawing = False
            if len(self._points) < 3:
                self.report({"WARNING"}, "Draw a larger closed region around the hole")
                return {"RUNNING_MODAL"}
            source = bpy.data.objects.get(self._source_name)
            polygon = list(self._points)
            region = self._window_region
            region_3d = self._region_3d
            self._cleanup(context)
            if not source:
                self.report({"ERROR"}, "Source object was removed")
                return {"CANCELLED"}

            from bpy_extras import view3d_utils

            source_bvh, _boundary_points = _evaluated_world_surface(source)
            if source_bvh is None:
                self.report({"ERROR"}, "The source has no usable surface geometry")
                return {"CANCELLED"}
            settings = context.scene.remi_settings
            screen_samples = _resample_screen_lasso(
                polygon,
                float(settings.targeted_ray_spacing),
            )
            ray_hits = []
            for screen in screen_samples:
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, region_3d, screen)
                ray_direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, screen)
                hit = source_bvh.ray_cast(ray_origin, ray_direction)
                if hit[0] is not None:
                    ray_hits.append((hit[0], hit[1], float(hit[3])))
            minimum_hits = max(6, int(len(screen_samples) * 0.35))
            if len(ray_hits) < minimum_hits:
                self.report(
                    {"ERROR"},
                    "Too much of the stroke missed the mesh. Draw the loop on the visible surface around the hole.",
                )
                return {"CANCELLED"}

            sorted_depths = sorted(depth for _point, _normal, depth in ray_hits)
            median_depth = sorted_depths[len(sorted_depths) // 2]
            depth_tolerance = (
                _world_bounds_diagonal(source) * float(settings.targeted_ray_depth_ratio)
            )
            ring_world = [
                point
                for point, _normal, depth in ray_hits
                if abs(depth - median_depth) <= depth_tolerance
            ]
            ring_normals = [
                normal
                for _point, normal, depth in ray_hits
                if abs(depth - median_depth) <= depth_tolerance
            ]
            if len(ring_world) < minimum_hits:
                self.report(
                    {"ERROR"},
                    "The stroke hit multiple depth layers. Lower Depth or redraw tightly on the front rim.",
                )
                return {"CANCELLED"}

            result, error, report = _create_surface_ring_patch(
                source,
                settings,
                ring_world,
                ring_normals=ring_normals,
            )
            if error:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}
            self.report(
                {"INFO"},
                f"Added {report.get('patch_faces', 0):,} patch faces to active mesh",

            )
            return {"FINISHED"}

        return {"RUNNING_MODAL"}

class Remi_OT_RepairHoles(Operator):
    """Prepare holes/cracks on the active mesh in-place using guide patches or legacy hole repair."""

    bl_idname = "remi.repair_holes"
    bl_label = "Repair Holes"
    bl_description = "Apply hole/crack repair to the active mesh in-place (no duplicate)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and context.active_object.type == "MESH" and context.mode == "OBJECT")

    def execute(self, context):
        settings = context.scene.remi_settings
        source = context.active_object
        if settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
            repaired, error, report = _guided_hole_patches(source, settings)
            if error:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}
            faces = report.get("patch_faces", 0)
            coverage = report.get("boundary_coverage", 1.0)
            if report.get("guide_method") == "VOLUME":
                self.report(
                    {"INFO"},
                    f"Patched '{repaired.name}' with {faces:,} fitted volume-patch faces",
                )
            else:
                self.report(
                    {"INFO"},
                    f"Patched '{repaired.name}' with {faces:,} patch faces; "
                    f"{coverage:.0%} boundary coverage",
                )
            return {"FINISHED"}
        # In-place repair for HYBRID / BOUNDARY methods
        stats = _prepare_hole_repair(source, settings)
        close_distance = _hole_close_distance(source, settings)
        if close_distance > 0.0:
            gn_setup.apply_remi_modifier(
                obj=source,
                voxel_size=settings.voxel_size,
                hole_close_distance=close_distance,
                detail_recovery_distance=_detail_recovery_distance(source, settings),
            )
            _apply_modifiers(source)
        context.view_layer.objects.active = source
        source.select_set(True)
        self.report(
            {"INFO"},
            f"Patched '{source.name}': {stats['new_faces']} boundary patches, "
            f"close distance {close_distance:.5g}",
        )
        return {"FINISHED"}


class Remi_OT_BuildAlphaWrap(Operator):
    """Configure and compile the bundled CGAL Alpha Wrap helper."""

    bl_idname = "remi.build_alpha_wrap"
    bl_label = "Build Alpha Wrap Helper"
    bl_description = "Compile the bundled C++ helper with CMake and the installed CGAL development package"

    def execute(self, context):
        result = aw.build_helper()
        if not result.get("success"):
            self.report({"ERROR"}, result.get("error", "Could not build Alpha Wrap helper"))
            return {"CANCELLED"}
        context.scene.remi_settings.alpha_wrap_executable = result["executable"]
        self.report({"INFO"}, "Alpha Wrap helper built successfully")
        return {"FINISHED"}

class Remi_OT_ImportGLB(Operator):
    """Import a GLB file into the scene."""
    bl_idname = "remi.import_glb"
    bl_label = "Import GLB"
    bl_description = "Import a GLB/glTF file into the scene"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")  # type: ignore

    def execute(self, context):
        settings = context.scene.remi_settings

        if self.filepath:
            filepath = self.filepath
        elif settings.import_glb_path:
            filepath = settings.import_glb_path
        else:
            self.report({"ERROR"}, "No GLB file specified")
            return {"CANCELLED"}

        if not os.path.exists(filepath):
            self.report({"ERROR"}, f"File not found: {filepath}")
            return {"CANCELLED"}

        # Import GLB/glTF
        prev_objects = set(bpy.context.scene.objects)
        try:
            bpy.ops.import_scene.gltf(filepath=filepath)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to import GLB: {e}")
            return {"CANCELLED"}

        # Find imported objects
        new_objs = [o for o in bpy.context.scene.objects if o not in prev_objects]
        if not new_objs:
            self.report({"WARNING"}, "No objects imported (file may be empty)")
            return {"CANCELLED"}

        # Select the first imported mesh
        for obj in new_objs:
            if obj.type == "MESH":
                bpy.context.view_layer.objects.active = obj
                obj.select_set(True)
                break

        self.report({"INFO"}, f"Imported {len(new_objs)} object(s) from GLB")
        return {"FINISHED"}

    def invoke(self, context, event):
        settings = context.scene.remi_settings
        if settings.import_glb_path:
            self.filepath = settings.import_glb_path
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class Remi_OT_SDFRemesh(Operator):
    """Apply SDF voxel remesh to selected object (via geometry nodes on a copy)."""
    bl_idname = "remi.sdf_remesh"
    bl_label = "SDF Voxel Remesh"
    bl_description = "Duplicate selected object and apply SDF grid remesh via Geometry Nodes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings

        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        if settings.remesh_backend == "VOLUME":
            result, error, report = _closing_volume_remesh(obj, settings, "_remesh")
            if error:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}
            self.report(
                {"INFO"},
                f"Closing Volume created {report['faces']:,} faces; fitted "
                f"{report['surface_projected']:,} surface and {report['feature_fitted']:,} crease vertices",
            )
            return {"FINISHED"}

        # Duplicate/prepare the object (never touch the original).
        if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
            dup, error, report = _guided_hole_patches(obj, settings, "_remesh")
            if error:
                self.report({"ERROR"}, error)
                return {"CANCELLED"}
            if report.get("guide_method") == "VOLUME":
                self.report({"INFO"}, f"Added {report['patch_faces']:,} fitted volume-patch faces")
            else:
                self.report(
                    {"INFO"},
                    f"Added {report['patch_faces']:,} hole-patch faces "
                    f"({report['boundary_coverage']:.0%} boundary coverage)",
                )
        else:
            dup = _duplicate_object(obj, "_remesh")
        dup.select_set(True)
        bpy.context.view_layer.objects.active = dup
        _prepare_hole_repair(dup, settings)

        # Apply the SDF geometry nodes modifier
        gn_setup.apply_remi_modifier(
            obj=dup,
            voxel_size=settings.voxel_size,
            hole_close_distance=_hole_close_distance(dup, settings),
            detail_recovery_distance=_detail_recovery_distance(dup, settings),
            fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
            smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
        )

        self.report({"INFO"}, f"Applied SDF remesh to '{dup.name}'")
        return {"FINISHED"}


class Remi_OT_ApplyRemesh(Operator):
    """Apply the SDF remesh modifier, converting it to real geometry."""
    bl_idname = "remi.apply_remesh"
    bl_label = "Apply Remesh"
    bl_description = "Apply the geometry nodes modifier to bake the remeshed geometry"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        # Find and apply AR modifier
        group = gn_setup.ensure_remi_node_group()
        found = False
        for mod in obj.modifiers:
            is_remi_group = (
                mod.type == "NODES"
                and mod.node_group
                and (
                    mod.node_group == group
                    or mod.node_group.name.startswith("Remi_SDF_Remesh")
                )
            )
            if is_remi_group:
                _apply_modifiers(obj)
                found = True
                break

        if not found:
            self.report({"ERROR"}, "No AR_SDF_Remesh modifier found on active object")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Applied remesh on '{obj.name}'")
        return {"FINISHED"}


class Remi_OT_Decimate(Operator):
    """Export to OBJ and run PyMeshLab quadric edge collapse decimation."""
    bl_idname = "remi.decimate"
    bl_label = "Decimate (MeshLab)"
    bl_description = "Export active object to OBJ and run MeshLab quadric edge collapse"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings

        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        # Check PyMeshLab
        if not mlw.ensure_pymeshlab():
            self.report({"ERROR"}, "PyMeshLab is not installed and could not be installed. "
                                    "Please run Blender with admin/sudo and it will auto-install.")
            return {"CANCELLED"}

        # Setup temp paths
        temp_dir = _get_temp_dir()
        base_name = bpy.path.clean_name(obj.name)
        input_ply = os.path.join(temp_dir, f"{base_name}_input.ply")
        output_ply = os.path.join(temp_dir, f"{base_name}_decimated.ply")

        # Export to PLY (no axis conversion — PyMeshLab compatible)
        self.report({"INFO"}, "Exporting to PLY...")
        if not _export_ply(obj, input_ply):
            self.report({"ERROR"}, "PLY export failed")
            return {"CANCELLED"}

        # Run decimation
        self.report({"INFO"}, f"Running {settings.decimation_passes} decimation pass(es)...")
        results = mlw.run_multi_pass_decimation(
            input_path=input_ply,
            output_path=output_ply,
            passes=settings.decimation_passes,
            target_percentage=settings.target_percentage,
            preserve_detail=settings.decimation_preserve_detail,
        )

        # Check results
        for r in results:
            if not r["success"]:
                self.report({"ERROR"}, f"Decimation pass {r['pass']} failed: {r.get('error')}")
                return {"CANCELLED"}
            print(f"Remi: Pass {r['pass']}: {r.get('input_faces', '?')} → {r.get('output_faces', '?')} faces")

        # Import result back (PLY import)
        self.report({"INFO"}, "Importing decimated result...")
        new_obj = _import_ply(output_ply)
        if new_obj:
            new_obj.name = obj.name + settings.output_name_suffix
            # Vertices are already at world-space coords (baked during export),
            # so the object sits at origin with correct geometry.
            self.report({"INFO"}, f"Decimated model imported as '{new_obj.name}'")
        else:
            self.report({"ERROR"}, "Failed to import decimated PLY")
            return {"CANCELLED"}

        # Cleanup temp files
        try:
            os.remove(input_ply)
            os.remove(output_ply)
        except OSError:
            pass

        return {"FINISHED"}


class Remi_OT_AutoRemesher(Operator):
    """Run AutoRemesher external tool on the active mesh."""
    bl_idname = "remi.autoremesher"
    bl_label = "AutoRemesher (External)"
    bl_description = "Run the external AutoRemesher executable on the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        executable = arm.resolve_executable(settings.autoremesher_executable)
        error = arm.validate_executable(executable)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        temp_dir = _get_temp_dir()
        base_name = bpy.path.clean_name(obj.name)
        input_obj = os.path.join(temp_dir, f"{base_name}_ar_input.obj")
        output_obj = os.path.join(temp_dir, f"{base_name}_ar_output.obj")
        report_path = os.path.join(temp_dir, f"{base_name}_ar_report.txt")

        self.report({"INFO"}, "Exporting to OBJ for AutoRemesher...")
        if not _export_obj_for_tool(obj, input_obj):
            self.report({"ERROR"}, "OBJ export failed")
            return {"CANCELLED"}

        command = arm.build_command(
            executable,
            Path(input_obj),
            Path(output_obj),
            Path(report_path),
            target_quads=settings.ar_target_quads,
            edge_scaling=settings.ar_edge_scaling,
            sharp_edge=settings.ar_sharp_edge,
            smooth_normal=settings.ar_smooth_normal,
            adaptivity=settings.ar_adaptivity,
        )

        self.report({"INFO"}, "Running AutoRemesher...")
        result = subprocess.run(
            command,
            cwd=str(executable.parent),
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip()
            self.report({"ERROR"}, msg or "AutoRemesher failed")
            return {"CANCELLED"}

        if not os.path.isfile(output_obj):
            self.report({"ERROR"}, "AutoRemesher did not produce output file")
            return {"CANCELLED"}

        self.report({"INFO"}, "Importing AutoRemesher result...")
        new_obj = _import_obj_result(output_obj)
        if new_obj:
            new_obj.name = obj.name + "_autoremesh"
            if settings.ar_hide_original:
                obj.hide_set(True)
            bpy.context.view_layer.objects.active = new_obj
            new_obj.select_set(True)
            self.report({"INFO"}, f"AutoRemesher result imported as '{new_obj.name}'")
        else:
            self.report({"ERROR"}, "Failed to import AutoRemesher result")
            return {"CANCELLED"}

        # Cleanup
        for f in (input_obj, output_obj, report_path):
            try:
                os.remove(f)
            except OSError:
                pass

        return {"FINISHED"}


class Remi_BakeOperatorMixin:
    """Bake albedo, roughness, normal, and AO maps onto the active mesh."""
    bake_passes = ("diffuse", "roughness", "normal", "ao")

    @classmethod
    def poll(cls, context):
        # Keep the buttons available; execute() gives a useful selection hint
        # when a source mesh has not been selected yet.
        return bool(
            context.mode == "OBJECT"
            and context.active_object
            and context.active_object.type == "MESH"
        )

    def execute(self, context):
        # The ACTIVE object receives the bake (= the remeshed/optimized mesh)
        # The other SELECTED objects provide the detail (= the original meshes)
        target = context.active_object
        sources = [o for o in context.selected_objects if o != target and o.type == "MESH"]
        if not sources:
            self.report({"ERROR"}, "Select original mesh(es) first, then Shift-select the target "
                                     "(optimized mesh) last so it becomes active")
            return {"CANCELLED"}

        s = context.scene.remi_settings
        result = baking.bake_textures(
            sources, target,
            texture_size=s.bake_texture_size,
            uv_method=s.bake_uv_method,
            uv_island_margin=s.bake_uv_island_margin,
            auto_unwrap=s.bake_auto_unwrap,
            recalc_normals=s.bake_recalc_normals,
            cage_extrusion=s.bake_cage_extrusion,
            max_ray_distance=s.bake_max_ray_distance,
            passes=self.bake_passes,
        )
        if result["success"]:
            self.report({"INFO"}, f"Baked {', '.join(self.bake_passes)} → {target.name}")
        else:
            self.report({"ERROR"}, result.get("error", "Baking failed"))
            return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_BakeAllMaps(Remi_BakeOperatorMixin, Operator):
    """Bake all available maps onto the active mesh."""

    bl_idname = "remi.bake_all_maps"
    bl_label = "Bake All Maps"
    bl_description = "Bake albedo, roughness, normal, and AO maps onto the active target mesh"
    bl_options = {"REGISTER", "UNDO"}


class Remi_OT_BakeDiffuse(Remi_BakeOperatorMixin, Operator):
    """Bake only the albedo/diffuse map onto the active mesh."""

    bl_idname = "remi.bake_diffuse"
    bl_label = "Bake Albedo"
    bl_description = "Bake only the diffuse/albedo map onto the active target mesh"
    bake_passes = ("diffuse",)


class Remi_OT_BakeRoughness(Remi_BakeOperatorMixin, Operator):
    """Bake only the roughness map onto the active mesh."""

    bl_idname = "remi.bake_roughness"
    bl_label = "Bake Roughness"
    bl_description = "Bake only the roughness map onto the active target mesh"
    bake_passes = ("roughness",)


class Remi_OT_BakeNormal(Remi_BakeOperatorMixin, Operator):
    """Bake only the tangent-space normal map onto the active mesh."""

    bl_idname = "remi.bake_normal"
    bl_label = "Bake Normal"
    bl_description = "Bake only the tangent-space normal map onto the active target mesh"
    bake_passes = ("normal",)


class Remi_OT_BakeAO(Remi_BakeOperatorMixin, Operator):
    """Bake only ambient occlusion onto the active mesh."""

    bl_idname = "remi.bake_ao"
    bl_label = "Bake Ambient Occlusion"
    bl_description = "Bake only ambient occlusion onto the active target mesh"
    bake_passes = ("ao",)


class Remi_OT_FullPipeline(Operator):
    """Run the full Remi pipeline — modal (non‑blocking) with progress."""
    bl_idname = "remi.full_pipeline"
    bl_label = "Remi Pipeline"
    bl_description = "SDF Remesh → Decimate → [AutoRemesher] → [Bake Textures]"
    bl_options = {"REGISTER"}

    # ── Modal state ──────────────────────────────────────────
    pipe_state: bpy.props.StringProperty(default="")
    pipe_step: bpy.props.IntProperty(default=0)
    pipe_total: bpy.props.IntProperty(default=1)
    pipe_next: bpy.props.StringProperty(default="")
    pipe_obj: bpy.props.StringProperty(default="")
    pipe_dup: bpy.props.StringProperty(default="")
    pipe_cur: bpy.props.StringProperty(default="")

    def status(self, context, msg):
        self.report({"INFO"}, msg)
        context.window_manager.progress_update(self.pipe_step)

    def fail(self, context, msg):
        self.report({"ERROR"}, msg)

    def cleanup(self, context):
        if hasattr(self, "_timer") and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.window_manager.progress_end()
        if hasattr(self, "_subproc") and self._subproc:
            try:
                self._subproc.kill()
            except Exception:
                pass
            self._subproc = None
        for f in getattr(self, "_files", []):
            try:
                os.remove(f)
            except OSError:
                pass
        self.pipe_state = ""

    def go(self, context, state, msg=None):
        if msg:
            self.status(context, msg)
        self.pipe_state = state

    def start_subproc(self, cmd, next_state, context, status_msg):
        self._subproc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.pipe_next = next_state
        self.go(context, "_SUB", status_msg)

    def _total(self, settings):
        n = 0
        n += 1 if settings.use_sdf_remesh else 0
        n += 1 if settings.use_decimation else 0
        n += 1 if settings.use_autoremesher else 0
        n += 1 if settings.use_baking else 0
        return n or 1

    # ── Synchronous fallback for background mode ───────────────
    def _sync_run(self, context, settings):
        """Run the whole pipeline synchronously (no modal)."""
        obj = context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object")
            return {"CANCELLED"}
        if settings.use_baking and not any((
            settings.use_sdf_remesh,
            settings.use_decimation,
            settings.use_autoremesher,
        )):
            self.report({"ERROR"}, "Enable a remesh or decimation stage before baking in the full pipeline")
            return {"CANCELLED"}

        current = None
        dup = None

        if settings.use_sdf_remesh:
            if settings.remesh_backend == "VOLUME":
                self.report({"INFO"}, "Running fitted Closing Volume remesh...")
                dup, error, volume_report = _closing_volume_remesh(obj, settings, "_remesh")
                if error:
                    self.report({"ERROR"}, error)
                    return {"CANCELLED"}
                self.report(
                    {"INFO"},
                    f"Closing Volume fitted {volume_report.get('feature_fitted', 0):,} crease vertices",
                )
            else:
                if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
                    self.report({"INFO"}, "Preparing guide-derived hole patches...")
                    dup, error, patch_report = _guided_hole_patches(obj, settings, "_remesh")
                    if error:
                        self.report({"ERROR"}, error)
                        return {"CANCELLED"}
                    coverage = patch_report.get("boundary_coverage", 0.0)
                    if patch_report.get("guide_method") == "VOLUME":
                        self.report(
                            {"INFO"},
                            f"Volume preparation retained {patch_report.get('patch_faces', 0):,} fitted patch faces",
                        )
                    else:
                        report_type = "INFO" if coverage >= settings.alpha_wrap_coverage_target else "WARNING"
                        self.report({report_type}, f"Hole preparation: {coverage:.0%} boundary coverage")
                else:
                    self.report({"INFO"}, "SDF remeshing...")
                    dup = _duplicate_object(obj, "_remesh")
                    context.view_layer.objects.active = dup
                    dup.select_set(True)
                    _prepare_hole_repair(dup, settings)
                gn_setup.apply_remi_modifier(
                    obj=dup,
                    voxel_size=settings.voxel_size,
                    hole_close_distance=_hole_close_distance(dup, settings),
                    detail_recovery_distance=_detail_recovery_distance(dup, settings),
                    fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
                    smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
                )
                _apply_modifiers(dup)

        if settings.use_decimation:
            self.report({"INFO"}, "Decimating...")
            if not mlw.ensure_pymeshlab():
                self.report({"ERROR"}, "PyMeshLab not available")
                return {"CANCELLED"}
            td = _get_temp_dir()
            source = dup if dup else obj
            base = bpy.path.clean_name(source.name)
            inp = os.path.join(td, f"{base}_input.ply")
            out = os.path.join(td, f"{base}_decimated.ply")
            if not _export_ply(source, inp):
                self.report({"ERROR"}, "PLY export failed")
                return {"CANCELLED"}
            mlw.run_multi_pass_decimation(
                input_path=inp, output_path=out,
                passes=settings.decimation_passes,
                target_percentage=settings.target_percentage,
                preserve_detail=settings.decimation_preserve_detail)
            current = _import_ply(out)
            if not current:
                self.report({"ERROR"}, "Failed to import decimated mesh")
                return {"CANCELLED"}
            if dup:
                current.name = dup.name
                bpy.data.objects.remove(dup, do_unlink=True)
                dup = None
            for f in (inp, out):
                try:
                    os.remove(f)
                except OSError:
                    pass

        if settings.use_autoremesher:
            self.report({"INFO"}, "AutoRemesher...")
            source = current if current else (dup if dup else obj)
            exe = arm.resolve_executable(settings.autoremesher_executable)
            err = arm.validate_executable(exe)
            if err:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}
            td = _get_temp_dir()
            base = bpy.path.clean_name(source.name)
            ar_in = os.path.join(td, f"{base}_ar_in.obj")
            ar_out = os.path.join(td, f"{base}_ar_out.obj")
            ar_rpt = os.path.join(td, f"{base}_ar_report.txt")
            if not _export_obj_for_tool(source, ar_in):
                return {"CANCELLED"}
            cmd = arm.build_command(
                exe, Path(ar_in), Path(ar_out), Path(ar_rpt),
                target_quads=settings.ar_target_quads,
                edge_scaling=settings.ar_edge_scaling,
                sharp_edge=settings.ar_sharp_edge,
                smooth_normal=settings.ar_smooth_normal,
                adaptivity=settings.ar_adaptivity,
            )
            proc = subprocess.run(cmd, cwd=str(exe.parent), capture_output=True, text=True)
            if proc.returncode != 0:
                self.report({"ERROR"}, proc.stderr.strip() or "AutoRemesher failed")
                return {"CANCELLED"}
            if not os.path.isfile(ar_out):
                self.report({"ERROR"}, "AutoRemesher produced no output")
                return {"CANCELLED"}
            if current:
                bpy.data.objects.remove(current, do_unlink=True)
            elif dup:
                bpy.data.objects.remove(dup, do_unlink=True)
                dup = None
            current = _import_obj_result(ar_out)
            if not current:
                self.report({"ERROR"}, "Failed to import AutoRemesher result")
                return {"CANCELLED"}
            current.name = base
            for f in (ar_in, ar_out, ar_rpt):
                try:
                    os.remove(f)
                except OSError:
                    pass

        if settings.use_baking:
            self.report({"INFO"}, "Baking textures...")
            target = current if current else (dup if dup else obj)
            final_name = obj.name + settings.output_name_suffix
            result = baking.bake_textures(
                obj, target,
                texture_size=settings.bake_texture_size,
                final_name=final_name,
                uv_method=settings.bake_uv_method,
                uv_island_margin=settings.bake_uv_island_margin,
                auto_unwrap=settings.bake_auto_unwrap,
                recalc_normals=settings.bake_recalc_normals,
                cage_extrusion=settings.bake_cage_extrusion,
                max_ray_distance=settings.bake_max_ray_distance,
                )
            if not result["success"]:
                self.report({"ERROR"}, result.get("error", "Baking failed"))
                return {"CANCELLED"}
            target.name = final_name
        elif current:
            current.name = obj.name + settings.output_name_suffix
        elif dup:
            dup.name = obj.name + settings.output_name_suffix

        self.report({"INFO"}, "Remi pipeline complete!")
        return {"FINISHED"}

    def execute(self, context):
        # In background mode, run synchronously (modal timers don't fire)
        if bpy.app.background or not context.window:
            return self._sync_run(context, context.scene.remi_settings)

        # In GUI mode, run modally for non-blocking progress
        settings = context.scene.remi_settings
        obj = context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object")
            return {"CANCELLED"}
        if settings.use_baking and not any((
            settings.use_sdf_remesh,
            settings.use_decimation,
            settings.use_autoremesher,
        )):
            self.report({"ERROR"}, "Enable a remesh or decimation stage before baking in the full pipeline")
            return {"CANCELLED"}

        self.pipe_obj = obj.name
        self.pipe_step = 0
        self.pipe_total = self._total(settings)
        self.pipe_dup = ""
        # pipe_cur always identifies the latest process result.  Starting it
        # at the source also makes Decimation-only and AutoRemesher-only runs
        # valid; SDF replaces it with its duplicate below.
        self.pipe_cur = obj.name
        self.pipe_next = ""
        self._subproc = None
        self._files = []
        self._timer = context.window_manager.event_timer_add(0.15, window=context.window)

        context.window_manager.modal_handler_add(self)
        context.window_manager.progress_begin(0, self.pipe_total)
        # Start at the first enabled step
        if settings.use_sdf_remesh:
            self.pipe_state = "SDF"
        elif settings.use_decimation:
            self.pipe_state = "EXPORT"
        elif settings.use_autoremesher:
            self.pipe_state = "AR_EXPORT"
        elif settings.use_baking:
            self.pipe_state = "BAKE"
        else:
            self.pipe_state = "DONE"
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        settings = context.scene.remi_settings

        # ── Subprocess polling ──────────────────────────────────
        sp = getattr(self, "_subproc", None)
        if sp is not None:
            # Read progress lines from stdout (non-blocking)
            sout = getattr(sp, "stdout", None)
            if sout is not None:
                r, _, _ = select.select([sout], [], [], 0)
                while r and sout:
                    line = sout.readline()
                    if not line:
                        break
                    try:
                        data = json.loads(line.strip())
                        if "pass" in data and "passes" in data:
                            p, tp = data["pass"], data["passes"]
                            self.status(context, f"Decimating... pass {p}/{tp}")
                    except json.JSONDecodeError:
                        pass
                    r, _, _ = select.select([sout], [], [], 0)

            ret = sp.poll()
            if ret is None:
                return {"RUNNING_MODAL"}

            # Subprocess finished
            self._subproc = None
            if ret != 0:
                err = (sp.stderr.read() or "").strip()
                self.fail(context, err or "Subprocess failed")
                self.cleanup(context)
                return {"CANCELLED"}

            ns = self.pipe_next
            self.pipe_next = ""
            self.go(context, ns)
            return {"RUNNING_MODAL"}

        state = self.pipe_state
        if not state:
            return {"RUNNING_MODAL"}

        # ── SDF Remesh ──────────────────────────────────────────
        if state == "SDF":
            self.pipe_step += 1
            obj = bpy.data.objects.get(self.pipe_obj)
            if not obj:
                self.fail(context, "Source object lost")
                self.cleanup(context)
                return {"CANCELLED"}
            if settings.remesh_backend == "VOLUME":
                dup, error, volume_report = _closing_volume_remesh(obj, settings, "_remesh")
                if error:
                    self.fail(context, error)
                    self.cleanup(context)
                    return {"CANCELLED"}
                self.report(
                    {"INFO"},
                    f"Closing Volume fitted {volume_report.get('feature_fitted', 0):,} crease vertices",
                )
            else:
                if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
                    dup, error, patch_report = _guided_hole_patches(obj, settings, "_remesh")
                    if error:
                        self.fail(context, error)
                        self.cleanup(context)
                        return {"CANCELLED"}
                    coverage = patch_report.get("boundary_coverage", 0.0)
                    if patch_report.get("guide_method") == "VOLUME":
                        self.report(
                            {"INFO"},
                            f"Volume preparation retained {patch_report.get('patch_faces', 0):,} fitted patch faces",
                        )
                    else:
                        report_type = "INFO" if coverage >= settings.alpha_wrap_coverage_target else "WARNING"
                        self.report({report_type}, f"Hole preparation: {coverage:.0%} boundary coverage")
                else:
                    dup = _duplicate_object(obj, "_remesh")
                    context.view_layer.objects.active = dup
                    dup.select_set(True)
                    _prepare_hole_repair(dup, settings)
                gn_setup.apply_remi_modifier(
                    obj=dup,
                    voxel_size=settings.voxel_size,
                    hole_close_distance=_hole_close_distance(dup, settings),
                    detail_recovery_distance=_detail_recovery_distance(dup, settings),
                    fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
                    smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
                )
                _apply_modifiers(dup)
            self.pipe_dup = dup.name
            self.pipe_cur = dup.name
            stage_label = "SDF remesh"
            if settings.use_decimation:
                self.go(context, "EXPORT", f"{stage_label} done, exporting...")
            elif settings.use_autoremesher:
                self.pipe_step += 1  # skip decimation step
                self.go(context, "AR_EXPORT", "Exporting for AutoRemesher...")
            elif settings.use_baking:
                self.pipe_step += 2  # skip decimation + autoremesher
                self.go(context, "BAKE", "Baking textures...")
            else:
                self.go(context, "DONE", "Finalizing...")

        # ── Export PLY + start PyMeshLab subprocess ─────────────
        elif state == "EXPORT":
            current = bpy.data.objects.get(self.pipe_cur)
            if not current:
                self.fail(context, "Mesh lost before decimation")
                self.cleanup(context)
                return {"CANCELLED"}
            td = _get_temp_dir()
            base = bpy.path.clean_name(current.name)
            inp = os.path.join(td, f"{base}_input.ply")
            out = os.path.join(td, f"{base}_decimated.ply")
            self._files += [inp, out]
            if not _export_ply(current, inp):
                self.fail(context, "PLY export failed")
                self.cleanup(context)
                return {"CANCELLED"}
            self.pipe_step += 1
            worker = os.path.join(os.path.dirname(__file__), "_decimate_worker.py")
            self.start_subproc(
                [sys.executable, worker, inp, out,
                 str(settings.target_percentage), str(settings.decimation_passes),
                 str(settings.decimation_preserve_detail)],
                "IMPORT_DEC", context,
                f"Decimating... pass 1/{settings.decimation_passes}")

        # ── Import decimated result ─────────────────────────────
        elif state == "IMPORT_DEC":
            source = bpy.data.objects.get(self.pipe_cur)
            base = bpy.path.clean_name(source.name) if source else "remesh"
            out = os.path.join(_get_temp_dir(), f"{base}_decimated.ply")
            if not os.path.isfile(out):
                self.fail(context, "Decimated PLY not found")
                self.cleanup(context)
                return {"CANCELLED"}
            current = _import_ply(out)
            if not current:
                self.fail(context, "Failed to import decimated mesh")
                self.cleanup(context)
                return {"CANCELLED"}
            if source and source.name == self.pipe_dup:
                current.name = source.name
                bpy.data.objects.remove(source, do_unlink=True)
                self.pipe_dup = ""
            self.pipe_cur = current.name
            if settings.use_autoremesher:
                self.pipe_step += 1
                self.go(context, "AR_EXPORT", "Exporting for AutoRemesher...")
            elif settings.use_baking:
                self.pipe_step += 1
                self.go(context, "BAKE", "Baking textures...")
            else:
                self.go(context, "DONE", "Finalizing...")

        # ── AutoRemesher: export OBJ + start subprocess ─────────
        elif state == "AR_EXPORT":
            current = bpy.data.objects.get(self.pipe_cur)
            if not current:
                self.fail(context, "Mesh lost before AutoRemesher")
                self.cleanup(context)
                return {"CANCELLED"}
            exe = arm.resolve_executable(settings.autoremesher_executable)
            err = arm.validate_executable(exe)
            if err:
                self.fail(context, err)
                self.cleanup(context)
                return {"CANCELLED"}
            td = _get_temp_dir()
            base = bpy.path.clean_name(current.name)
            ar_in = os.path.join(td, f"{base}_ar_in.obj")
            ar_out = os.path.join(td, f"{base}_ar_out.obj")
            ar_rpt = os.path.join(td, f"{base}_ar_report.txt")
            self._files += [ar_in, ar_out, ar_rpt]
            if not _export_obj_for_tool(current, ar_in):
                self.fail(context, "OBJ export failed")
                self.cleanup(context)
                return {"CANCELLED"}
            cmd = arm.build_command(
                exe, Path(ar_in), Path(ar_out), Path(ar_rpt),
                target_quads=settings.ar_target_quads,
                edge_scaling=settings.ar_edge_scaling,
                sharp_edge=settings.ar_sharp_edge,
                smooth_normal=settings.ar_smooth_normal,
                adaptivity=settings.ar_adaptivity,
            )
            self.start_subproc(cmd, "AR_IMPORT", context, "AutoRemesher running...")

        # ── Import AutoRemesher result ──────────────────────────
        elif state == "AR_IMPORT":
            current = bpy.data.objects.get(self.pipe_cur)
            base = bpy.path.clean_name(current.name) if current else "ar"
            ar_out = os.path.join(_get_temp_dir(), f"{base}_ar_out.obj")
            if not os.path.isfile(ar_out):
                self.fail(context, "AutoRemesher produced no output")
                self.cleanup(context)
                return {"CANCELLED"}
            rem = current.name if current else ""
            # Never delete the user's original in an AutoRemesher-only run.
            if current and current.name != self.pipe_obj:
                bpy.data.objects.remove(current, do_unlink=True)
            new_obj = _import_obj_result(ar_out)
            if not new_obj:
                self.fail(context, "Failed to import AutoRemesher result")
                self.cleanup(context)
                return {"CANCELLED"}
            new_obj.name = rem or "remesh_ar"
            self.pipe_cur = new_obj.name
            if settings.use_baking:
                self.pipe_step += 1
                self.go(context, "BAKE", "Baking textures...")
            else:
                self.go(context, "DONE", "Finalizing...")

        # ── Bake textures ───────────────────────────────────────
        elif state == "BAKE":
            src = bpy.data.objects.get(self.pipe_obj)
            cur = bpy.data.objects.get(self.pipe_cur)
            if not src or not cur:
                self.fail(context, "Objects missing for baking")
                self.cleanup(context)
                return {"CANCELLED"}
            if src == cur:
                self.fail(context, "Baking needs a generated target; enable a remesh or decimation stage")
                self.cleanup(context)
                return {"CANCELLED"}
            final_name = src.name + settings.output_name_suffix
            result = baking.bake_textures(
                src, cur,
                texture_size=settings.bake_texture_size,
                final_name=final_name,
                uv_method=settings.bake_uv_method,
                uv_island_margin=settings.bake_uv_island_margin,
                auto_unwrap=settings.bake_auto_unwrap,
                recalc_normals=settings.bake_recalc_normals,
                cage_extrusion=settings.bake_cage_extrusion,
                max_ray_distance=settings.bake_max_ray_distance,
            )
            if result["success"]:
                self.status(context, f"Baked: {', '.join(result['images'])}")
            else:
                self.fail(context, result.get("error", "Baking failed"))
                self.cleanup(context)
                return {"CANCELLED"}
            self.go(context, "DONE", "Finalizing...")

        # ── Finalize ────────────────────────────────────────────
        elif state == "DONE":
            src = bpy.data.objects.get(self.pipe_obj)
            cur = bpy.data.objects.get(self.pipe_cur)
            if cur and src:
                cur.name = src.name + settings.output_name_suffix
            self.pipe_step = self.pipe_total
            context.window_manager.progress_update(self.pipe_step)
            self.report({"INFO"}, "Remi pipeline complete!")
            self.cleanup(context)
            return {"FINISHED"}

        return {"RUNNING_MODAL"}


# ============================================================
# Registration
# ============================================================

classes = [
    Remi_OT_DrawHolePatch,
    Remi_OT_RepairHoles,
    Remi_OT_BuildAlphaWrap,
    Remi_OT_ImportGLB,
    Remi_OT_SDFRemesh,
    Remi_OT_ApplyRemesh,
    Remi_OT_Decimate,
    Remi_OT_AutoRemesher,
    Remi_OT_BakeAllMaps,
    Remi_OT_BakeDiffuse,
    Remi_OT_BakeRoughness,
    Remi_OT_BakeNormal,
    Remi_OT_BakeAO,
    Remi_OT_FullPipeline,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
