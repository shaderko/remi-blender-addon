"""Voxel and guided-volume remesh service."""

from __future__ import annotations

import bpy

from ... import gn_setup
from ...infrastructure.blender.mesh_objects import (
    apply_modifiers,
    duplicate_object,
    remove_mesh_object,
)
from ..repair.service import (
    _closing_volume_remesh,
    _detail_recovery_distance,
    _guided_hole_patches,
    _hole_close_distance,
    _prepare_hole_repair,
)


def create_candidate(
    source,
    settings,
    suffix="_remesh",
    apply_result=False,
    candidate=None,
    disk=None,
):
    """Build a remesh result without changing ``source``."""
    if settings.remesh_backend == "VOLUME":
        result, error, report = _closing_volume_remesh(
            source,
            settings,
            suffix,
            result=candidate,
        )
        if not error:
            report["backend"] = "VOLUME"
        return result, error, report

    report = {"backend": "VOXEL"}
    if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
        result, error, patch_report = _guided_hole_patches(
            source,
            settings,
            suffix,
            prepared=candidate,
            disk=disk,
        )
        if error:
            return None, error, patch_report
        report.update(patch_report)
    else:
        result = candidate or duplicate_object(source, suffix)
    try:
        result.select_set(True)
        bpy.context.view_layer.objects.active = result
        _prepare_hole_repair(result, settings)
        gn_setup.apply_remi_modifier(
            obj=result,
            voxel_size=settings.voxel_size,
            hole_close_distance=_hole_close_distance(result, settings),
            detail_recovery_distance=_detail_recovery_distance(result, settings),
            fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
            smooth_iterations=(
                settings.smoothing_iterations if settings.use_sdf_smoothing else 0
            ),
        )
        if apply_result:
            apply_modifiers(result)
    except Exception:
        remove_mesh_object(result)
        raise
    return result, "", report
