"""Reusable Blender mesh-object lifecycle operations."""

from __future__ import annotations

import bpy


def duplicate_object(
    obj: bpy.types.Object,
    suffix: str = "_copy",
) -> bpy.types.Object:
    """Create a data-independent processing duplicate without session identity."""
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.duplicate()
    duplicate = bpy.context.view_layer.objects.active
    duplicate.name = obj.name + suffix
    duplicate.pop("_remi_session_id", None)
    duplicate.pop("_remi_checkpoint_materials", None)
    obj.select_set(False)
    return duplicate


def remove_mesh_object(obj: bpy.types.Object | None) -> None:
    if not obj:
        return
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def apply_modifiers(obj: bpy.types.Object) -> None:
    """Apply every modifier on an object."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    for modifier in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        except Exception as exc:
            print(f"Remi: Could not apply modifier '{modifier.name}': {exc}")


def local_bounds_diagonal(obj: bpy.types.Object) -> float:
    if not obj.data.vertices:
        return 0.0
    minimum = obj.data.vertices[0].co.copy()
    maximum = minimum.copy()
    for vertex in obj.data.vertices[1:]:
        coordinate = vertex.co
        minimum.x = min(minimum.x, coordinate.x)
        minimum.y = min(minimum.y, coordinate.y)
        minimum.z = min(minimum.z, coordinate.z)
        maximum.x = max(maximum.x, coordinate.x)
        maximum.y = max(maximum.y, coordinate.y)
        maximum.z = max(maximum.z, coordinate.z)
    return (maximum - minimum).length


def world_bounds_diagonal(obj: bpy.types.Object) -> float:
    corners = [obj.matrix_world @ type(obj.location)(corner) for corner in obj.bound_box]
    if not corners:
        return 0.0
    minimum = corners[0].copy()
    maximum = minimum.copy()
    for coordinate in corners[1:]:
        minimum.x = min(minimum.x, coordinate.x)
        minimum.y = min(minimum.y, coordinate.y)
        minimum.z = min(minimum.z, coordinate.z)
        maximum.x = max(maximum.x, coordinate.x)
        maximum.y = max(maximum.y, coordinate.y)
        maximum.z = max(maximum.z, coordinate.z)
    return (maximum - minimum).length
