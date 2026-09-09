"""Repair geometry mechanisms shared by workflow and Blender adapters."""

from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from ... import alpha_wrap as aw
from ... import gn_setup
from ...infrastructure.blender.mesh_exchange import (
    export_ply as _export_ply,
    import_ply as _import_ply,
)
from ...infrastructure.blender.mesh_objects import (
    apply_modifiers as _apply_modifiers,
    duplicate_object as _duplicate_object,
    local_bounds_diagonal as _local_bounds_diagonal,
    remove_mesh_object as _remove_mesh_object,
    world_bounds_diagonal as _world_bounds_diagonal,
)
from ...workflow.disk_service import SessionDiskService


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
    disk=None,
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
        with SessionDiskService.operation_workspace(disk, "alpha-wrap") as workspace:
            input_path = str(workspace / "input.ply")
            output_path = str(workspace / "wrapped.ply")
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
    prepared=None,
):
    """Copy only selected guide patches onto evaluated source geometry."""
    guide_mesh = guide.data
    guide_face_count = len(guide_mesh.polygons)
    prepared = prepared or _duplicate_object(source, suffix)
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
    prepared=None,
    disk=None,
) -> tuple:
    """Keep the original mesh and add only gap-spanning faces from an Alpha Wrap guide."""
    source_bvh, boundary_points = _evaluated_world_surface(source)
    if source_bvh is None:
        return None, "Could not build a surface index for the original mesh", {}
    if not boundary_points:
        prepared = prepared or _duplicate_object(source, suffix)
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
            disk=disk,
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
        prepared=prepared,
    )

def _volume_hole_patches(
    source: bpy.types.Object,
    settings,
    suffix: str = "_prepared",
    prepared=None,
) -> tuple:
    """Use a fine SDF closing only as a guide, retaining its hole patches."""
    source_bvh, boundary_points = _evaluated_world_surface(source)
    if source_bvh is None:
        return None, "Could not build a surface index for the original mesh", {}
    if not boundary_points:
        prepared = prepared or _duplicate_object(source, suffix)
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
        prepared=prepared,
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
    result=None,
):
    """Triangulate and fair a local membrane bounded by ray hits on the source.

    The patch is written onto a prepared duplicate. The source mesh, object
    data, and modifier stack remain untouched.
    """
    from mathutils.geometry import tessellate_polygon

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

        # Apply evaluated geometry only on the duplicate, then transfer the
        # local patch into that prepared copy.
        result = result or _duplicate_object(source, suffix)
        _apply_modifiers(result)
        bm = bmesh.new()
        try:
            bm.from_mesh(result.data)
            transferred = {vertex: bm.verts.new(vertex.co) for vertex in patch_bm.verts}
            for face in patch_bm.faces:
                try:
                    bm.faces.new([transferred[vertex] for vertex in face.verts])
                except ValueError:
                    pass
            bm.to_mesh(result.data)
            result.data.update()
        except Exception:
            _remove_mesh_object(result)
            raise
        finally:
            bm.free()
    finally:
        patch_bm.free()

    bpy.ops.object.select_all(action="DESELECT")
    result.select_set(True)
    bpy.context.view_layer.objects.active = result
    return result, "", {
        "ray_hits": len(ring_world),
        "ring_vertices": len(ring_local),
        "initial_patch_faces": initial_faces,
        "patch_faces": patch_faces,
        "refinement_steps": refinement_steps,
    }

def _guided_hole_patches(
    source,
    settings,
    suffix="_prepared",
    prepared=None,
    disk=None,
):
    if settings.hole_repair_method == "ALPHA_WRAP":
        return _alpha_wrap_hole_patches(
            source,
            settings,
            suffix,
            prepared=prepared,
            disk=disk,
        )
    if settings.hole_repair_method == "VOLUME":
        return _volume_hole_patches(source, settings, suffix, prepared=prepared)
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

def _closing_volume_remesh(
    source,
    settings,
    suffix="_volume_remesh",
    result=None,
) -> tuple:
    """Create the slow hole-closing remesh and fit it back to source features."""
    result = result or _duplicate_object(source, suffix)
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

def _create_repair_candidate(
    source,
    settings,
    suffix="_prepared",
    candidate=None,
    disk=None,
):
    """Build a repaired result without changing ``source``."""
    if settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
        return _guided_hole_patches(
            source,
            settings,
            suffix,
            prepared=candidate,
            disk=disk,
        )

    prepared = candidate or _duplicate_object(source, suffix)
    try:
        _apply_modifiers(prepared)
        stats = _repair_boundary_holes(
            prepared,
            max_sides=settings.hole_max_sides,
            weld_distance=settings.hole_weld_distance,
        )
        close_distance = (
            _local_bounds_diagonal(prepared) * float(settings.hole_close_ratio)
            if settings.hole_repair_method == "HYBRID"
            else 0.0
        )
        if close_distance > 0.0:
            detail_recovery_distance = 0.0
            if settings.hole_detail_recovery:
                detail_recovery_distance = max(
                    _local_bounds_diagonal(prepared) * float(settings.hole_detail_ratio),
                    float(settings.voxel_size) * 2.0,
                )
            gn_setup.apply_remi_modifier(
                obj=prepared,
                voxel_size=settings.voxel_size,
                hole_close_distance=close_distance,
                detail_recovery_distance=detail_recovery_distance,
            )
            _apply_modifiers(prepared)
    except Exception:
        _remove_mesh_object(prepared)
        raise
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = prepared
    prepared.select_set(True)
    return prepared, "", {
        "new_faces": stats["new_faces"],
        "close_distance": close_distance,
        "guide_method": settings.hole_repair_method,
    }
