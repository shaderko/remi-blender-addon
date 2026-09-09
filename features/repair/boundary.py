"""Small-hole topology repair and hybrid distance policy."""

import bmesh
import bpy

from ...blender.mesh_objects import local_bounds_diagonal


def repair_boundary_holes(
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
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=float(weld_distance))
        boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
        result = (
            bmesh.ops.holes_fill(bm, edges=boundary_edges, sides=max(3, int(max_sides)))
            if boundary_edges
            else {"faces": []}
        )
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
        return {
            "boundary_edges_before": len(boundary_edges),
            "boundary_edges_after": sum(1 for edge in bm.edges if edge.is_boundary),
            "new_faces": len(new_faces),
        }
    finally:
        bm.free()


def hole_close_distance(obj: bpy.types.Object, settings) -> float:
    if not settings.use_hole_repair or settings.hole_repair_method != "HYBRID":
        return 0.0
    return local_bounds_diagonal(obj) * float(settings.hole_close_ratio)


def detail_recovery_distance(obj: bpy.types.Object, settings) -> float:
    if (
        not settings.use_hole_repair
        or not settings.hole_detail_recovery
        or settings.hole_repair_method != "HYBRID"
    ):
        return 0.0
    relative_reach = local_bounds_diagonal(obj) * float(settings.hole_detail_ratio)
    return max(relative_reach, float(settings.voxel_size) * 2.0)


def prepare_hole_repair(obj: bpy.types.Object, settings) -> dict:
    """Run the topology portion of the optional hybrid pre-repair."""
    if not settings.use_hole_repair or settings.hole_repair_method not in {
        "HYBRID",
        "BOUNDARY",
    }:
        return {"boundary_edges_before": 0, "boundary_edges_after": 0, "new_faces": 0}
    return repair_boundary_holes(
        obj,
        max_sides=settings.hole_max_sides,
        weld_distance=settings.hole_weld_distance,
    )


_repair_boundary_holes = repair_boundary_holes
_hole_close_distance = hole_close_distance
_detail_recovery_distance = detail_recovery_distance
_prepare_hole_repair = prepare_hole_repair
