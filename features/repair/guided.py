"""Repair geometry mechanisms shared by workflow and Blender adapters."""

from __future__ import annotations

import bmesh
import bpy
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from ...blender.mesh_objects import (
    apply_modifiers as _apply_modifiers,
    duplicate_object as _duplicate_object,
    remove_mesh_object as _remove_mesh_object,
)


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

def _guided_hole_patches(
    source,
    settings,
    suffix="_prepared",
    prepared=None,
    disk=None,
):
    if settings.hole_repair_method == "ALPHA_WRAP":
        from .alpha_wrap import alpha_wrap_hole_patches

        return alpha_wrap_hole_patches(
            source,
            settings,
            suffix,
            prepared=prepared,
            disk=disk,
        )
    if settings.hole_repair_method == "VOLUME":
        from .volume import volume_hole_patches

        return volume_hole_patches(source, settings, suffix, prepared=prepared)
    return None, "The selected repair method is not guide-based", {}
