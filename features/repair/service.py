"""Repair use case that selects a strategy and returns one candidate mesh."""

import bpy

from ... import gn_setup
from ...blender.mesh_objects import (
    apply_modifiers,
    duplicate_object,
    local_bounds_diagonal,
    remove_mesh_object,
)
from .boundary import (
    _detail_recovery_distance,
    _hole_close_distance,
    _prepare_hole_repair,
    repair_boundary_holes,
)
from .guided import (
    _evaluated_world_surface,
    _guided_hole_patches,
)
from .manual import _create_surface_ring_patch, _resample_screen_lasso
from .volume import _closing_volume_remesh


def create_candidate(source, settings, suffix="_prepared", candidate=None, disk=None):
    """Build a repaired result without changing ``source``."""
    if settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
        return _guided_hole_patches(
            source,
            settings,
            suffix,
            prepared=candidate,
            disk=disk,
        )

    prepared = candidate or duplicate_object(source, suffix)
    try:
        apply_modifiers(prepared)
        stats = repair_boundary_holes(
            prepared,
            max_sides=settings.hole_max_sides,
            weld_distance=settings.hole_weld_distance,
        )
        close_distance = (
            local_bounds_diagonal(prepared) * float(settings.hole_close_ratio)
            if settings.hole_repair_method == "HYBRID"
            else 0.0
        )
        detail_distance = 0.0
        if close_distance > 0.0:
            if settings.hole_detail_recovery:
                detail_distance = max(
                    local_bounds_diagonal(prepared) * float(settings.hole_detail_ratio),
                    float(settings.voxel_size) * 2.0,
                )
            gn_setup.apply_remi_modifier(
                obj=prepared,
                voxel_size=settings.voxel_size,
                hole_close_distance=close_distance,
                detail_recovery_distance=detail_distance,
            )
            apply_modifiers(prepared)
    except Exception:
        remove_mesh_object(prepared)
        raise
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = prepared
    prepared.select_set(True)
    return prepared, "", {
        "new_faces": stats["new_faces"],
        "close_distance": close_distance,
        "guide_method": settings.hole_repair_method,
    }


_create_repair_candidate = create_candidate

__all__ = (
    "create_candidate",
    "_create_repair_candidate",
    "_create_surface_ring_patch",
    "_evaluated_world_surface",
    "_resample_screen_lasso",
    "_closing_volume_remesh",
    "_detail_recovery_distance",
    "_guided_hole_patches",
    "_hole_close_distance",
    "_prepare_hole_repair",
)
