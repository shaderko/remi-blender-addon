"""Volume-guide hole repair and source-fitted closing remesh."""

import math

import bpy
from mathutils.kdtree import KDTree

from ...blender.mesh_objects import (
    apply_modifiers,
    duplicate_object,
    local_bounds_diagonal,
    remove_mesh_object,
    world_bounds_diagonal,
)
from ..remesh import geometry_nodes
from .guided import (
    _compose_source_with_guide_patches as compose_source_with_guide_patches,
    _dilate_guide_faces as dilate_guide_faces,
    _evaluated_world_surface as evaluated_world_surface,
    _guide_boundary_coverage as guide_boundary_coverage,
    _guide_patch_faces as guide_patch_faces,
)


def volume_hole_patches(source, settings, suffix="_prepared", prepared=None) -> tuple:
    """Use a fine SDF closing only as a guide, retaining its hole patches."""
    source_bvh, boundary_points = evaluated_world_surface(source)
    if source_bvh is None:
        return None, "Could not build a surface index for the original mesh", {}
    if not boundary_points:
        prepared = prepared or duplicate_object(source, suffix)
        apply_modifiers(prepared)
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

    guide = duplicate_object(source, "_volume_guide")
    apply_modifiers(guide)
    guide_voxel_size = max(
        float(settings.voxel_size) * float(settings.volume_guide_voxel_scale),
        0.0001,
    )
    close_distance = local_bounds_diagonal(guide) * float(settings.hole_close_ratio)
    geometry_nodes.apply_remi_modifier(
        obj=guide,
        voxel_size=guide_voxel_size,
        hole_close_distance=close_distance,
        detail_recovery_distance=0.0,
    )
    apply_modifiers(guide)
    if not guide.data.polygons:
        remove_mesh_object(guide)
        return None, "The volume guide produced no geometry", {}

    diagonal = world_bounds_diagonal(source)
    detection_distance = max(
        diagonal * float(settings.alpha_wrap_patch_ratio),
        guide_voxel_size * 1.5,
    )
    selected_faces = guide_patch_faces(guide, source_bvh, detection_distance, 0)
    boundary_tree = KDTree(len(boundary_points))
    for slot, point in enumerate(boundary_points):
        boundary_tree.insert(point, slot)
    boundary_tree.balance()
    boundary_band = max(
        diagonal * float(settings.hole_close_ratio) * 1.25,
        detection_distance * 3.0,
    )
    selected_faces = {
        face_index
        for face_index in selected_faces
        if (
            (nearest := source_bvh.find_nearest(
                guide.matrix_world @ guide.data.polygons[face_index].center
            ))
            and nearest[0] is not None
            and boundary_tree.find(nearest[0])[2] <= boundary_band
        )
    }
    selected_faces = dilate_guide_faces(
        guide,
        selected_faces,
        int(settings.alpha_wrap_patch_rings),
    )
    coverage = guide_boundary_coverage(
        guide,
        selected_faces,
        boundary_points,
        max(detection_distance * 2.0, guide_voxel_size * 4.0),
    )
    if not selected_faces:
        remove_mesh_object(guide)
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
    return compose_source_with_guide_patches(
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


def evaluated_world_sharp_edges(source, angle_degrees: float):
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


def closest_point_on_segment(point, start, end):
    direction = end - start
    length_squared = direction.length_squared
    if length_squared <= 1e-20:
        return start.copy()
    factor = max(0.0, min(1.0, (point - start).dot(direction) / length_squared))
    return start + direction * factor


def fit_volume_remesh_to_source(source, volume, settings) -> dict:
    """Fit a closing-volume result to source faces and sharp crease segments."""
    source_bvh, _boundary_points = evaluated_world_surface(source)
    if source_bvh is None:
        return {"surface_projected": 0, "feature_fitted": 0, "sharp_edges": 0}

    diagonal = world_bounds_diagonal(source)
    surface_reach = diagonal * float(settings.volume_surface_fit_ratio)
    world_scale = max(abs(value) for value in volume.matrix_world.to_scale())
    final_world_voxel = max(float(settings.voxel_size) * world_scale, 1e-7)
    feature_reach = final_world_voxel * float(settings.volume_feature_reach)
    inverse_world = volume.matrix_world.inverted()

    sharp_edges = []
    feature_tree = None
    if settings.volume_preserve_features:
        sharp_edges = evaluated_world_sharp_edges(source, settings.volume_feature_angle)
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
            crease_point = closest_point_on_segment(coordinate, start, end)
            crease_distance = (coordinate - crease_point).length
            if crease_distance <= feature_reach:
                factor = (
                    1.0
                    if crease_distance <= final_world_voxel * 0.75
                    else (1.0 - crease_distance / feature_reach) ** 2
                )
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


def closing_volume_remesh(source, settings, suffix="_volume_remesh", result=None) -> tuple:
    """Create the slow hole-closing remesh and fit it back to source features."""
    result = result or duplicate_object(source, suffix)
    apply_modifiers(result)
    volume_voxel = max(
        float(settings.voxel_size) * float(settings.volume_guide_voxel_scale),
        0.0001,
    )
    close_distance = local_bounds_diagonal(result) * float(settings.hole_close_ratio)
    geometry_nodes.apply_remi_modifier(
        obj=result,
        voxel_size=volume_voxel,
        hole_close_distance=close_distance,
        detail_recovery_distance=0.0,
        fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
        smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
    )
    apply_modifiers(result)
    if not result.data.polygons:
        remove_mesh_object(result)
        return None, "Closing Volume produced no geometry", {}
    report = fit_volume_remesh_to_source(source, result, settings)
    report.update(
        {
            "volume_voxel_size": volume_voxel,
            "close_distance": close_distance,
            "faces": len(result.data.polygons),
        }
    )
    bpy.ops.object.select_all(action="DESELECT")
    result.select_set(True)
    bpy.context.view_layer.objects.active = result
    return result, "", report


_volume_hole_patches = volume_hole_patches
_evaluated_world_sharp_edges = evaluated_world_sharp_edges
_closest_point_on_segment = closest_point_on_segment
_fit_volume_remesh_to_source = fit_volume_remesh_to_source
_closing_volume_remesh = closing_volume_remesh
