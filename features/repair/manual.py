"""Manual viewport-stroke sampling and local surface patch construction."""

import math

import bmesh
import bpy

from ...blender.mesh_objects import apply_modifiers, duplicate_object, remove_mesh_object


def resample_screen_lasso(points, spacing: float):
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


def create_surface_ring_patch(
    source,
    settings,
    ring_world,
    ring_normals=None,
    suffix="_targeted_patch",
    result=None,
):
    """Triangulate and fair a local membrane bounded by ray hits on the source."""
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
                edge
                for edge in patch_bm.edges
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
            for edge in patch_bm.edges
            if edge.is_boundary
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

        result = result or duplicate_object(source, suffix)
        apply_modifiers(result)
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
            remove_mesh_object(result)
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


_resample_screen_lasso = resample_screen_lasso
_create_surface_ring_patch = create_surface_ring_patch
