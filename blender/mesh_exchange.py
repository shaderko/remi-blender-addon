"""Selection-safe mesh import and export through Blender operators."""

from __future__ import annotations

import bpy


def _restore_selection(previous_active, previous_selected) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in previous_selected:
        obj.select_set(True)
    if previous_active:
        bpy.context.view_layer.objects.active = previous_active


def export_ply(obj: bpy.types.Object, filepath: str) -> bool:
    """Export one object as raw-axis PLY for PyMeshLab-compatible exchange."""
    previous_active = bpy.context.view_layer.objects.active
    previous_selected = bpy.context.selected_objects.copy()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.wm.ply_export(
            filepath=filepath,
            export_selected_objects=True,
            apply_modifiers=True,
        )
        return True
    except Exception as exc:
        print(f"Remi: PLY export failed: {exc}")
        return False
    finally:
        _restore_selection(previous_active, previous_selected)


def export_obj_for_tool(
    obj: bpy.types.Object,
    filepath: str,
    export_materials: bool = False,
) -> bool:
    """Export one OBJ for an external tool, optionally retaining materials."""
    previous_active = bpy.context.view_layer.objects.active
    previous_selected = bpy.context.selected_objects.copy()
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
            export_materials=export_materials,
            path_mode="ABSOLUTE" if export_materials else "AUTO",
        )
        return True
    except Exception:
        return False
    finally:
        _restore_selection(previous_active, previous_selected)


def import_obj_result(filepath: str) -> bpy.types.Object | None:
    """Import one OBJ result and restore the caller's selection."""
    previous_selected = bpy.context.selected_objects.copy()
    previous_active = bpy.context.view_layer.objects.active
    bpy.ops.wm.obj_import(
        filepath=filepath,
        use_split_objects=False,
        use_split_groups=False,
        forward_axis="NEGATIVE_Z",
        up_axis="Y",
    )
    imported = [obj for obj in bpy.context.selected_objects if obj not in previous_selected]
    mesh_objects = [obj for obj in imported if obj.type == "MESH"]
    _restore_selection(previous_active, previous_selected)
    return mesh_objects[0] if mesh_objects else None


def import_ply(filepath: str) -> bpy.types.Object | None:
    """Import one PLY result and restore the caller's selection."""
    previous_selected = bpy.context.selected_objects.copy()
    previous_active = bpy.context.view_layer.objects.active
    bpy.ops.wm.ply_import(filepath=filepath)
    imported = [obj for obj in bpy.context.selected_objects if obj not in previous_selected]
    mesh_objects = [obj for obj in imported if obj.type == "MESH"]
    _restore_selection(previous_active, previous_selected)
    return mesh_objects[0] if mesh_objects else None
