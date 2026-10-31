"""Texture workspace services shared by the UI and modal operator."""
from __future__ import annotations

from pathlib import Path

import bpy

from ...blender.gaussian_splats import is_splat_object
from ...blender.splat_projection import (
    apply_projection_material,
    import_projection_result,
    resolve_splat_source,
    source_label,
)


def appearance_source(mesh, settings):
    """Resolve a manual or linked splat source for the active mesh."""
    manual = getattr(settings, "source_object", None)
    source_object, archive = resolve_splat_source(mesh, manual=manual)
    return source_object, archive, source_label(source_object, archive)


def source_is_valid(source_object, archive):
    if source_object is not None:
        return is_splat_object(source_object)
    return archive is not None and Path(archive).is_file()


def apply_preview(mesh, mode):
    apply_projection_material(mesh, mode)


def import_result(
    mesh,
    paths,
    *,
    source_name,
    pack_images=True,
    normal_mode="PROJECTED",
    uv_name=None,
):
    result = import_projection_result(
        mesh,
        paths,
        source_label_value=source_name,
        pack_images=pack_images,
        normal_mode=normal_mode,
        uv_name=uv_name,
    )
    report = paths.get("report_data") or {}
    if report:
        mesh["remi_texture_coverage"] = float(report.get("coverage_mean", 0.0))
        mesh["remi_texture_filled"] = int(report.get("filled_holes", 0))
        mesh["remi_texture_padding"] = int(report.get("padding_texels", 0))
        mesh["remi_texture_texels"] = int(report.get("covered_texels", 0))
        mesh["remi_texture_size"] = int(report.get("texture_size", 0))
        mesh["remi_texture_flatness"] = float(report.get("flatness_mean", 0.0))
        mesh["remi_texture_method"] = str(report.get("method", "FIELD"))
        mesh["remi_texture_ray_misses"] = int(report.get("misses", 0))
        mesh["remi_texture_ray_steps_limited"] = int(report.get("step_limit_rays", 0))
        mesh["remi_texture_ray_clipped"] = int(report.get("cage_clipped_rays", 0))
        mesh["remi_texture_gap_rejected"] = int(report.get("gap_rejected_rays", 0))
        mesh["remi_texture_source_normal_texels"] = int(report.get("source_normal_texels", 0))
    return result


def active_mesh(context):
    obj = context.view_layer.objects.active
    if obj is not None and obj.type == "MESH" and context.mode == "OBJECT":
        return obj
    return None


def can_project(context):
    mesh = active_mesh(context)
    if mesh is None:
        return False
    state = context.window_manager.remi_texture
    if state.busy:
        return False
    session = getattr(context.window_manager, "remi_session", None)
    if session is not None and session.active:
        return False
    generation = getattr(context.window_manager, "remi_generation", None)
    if generation is not None and (generation.busy or generation.mesh_busy):
        return False
    return True


def external_python(context):
    settings = getattr(context.scene, "remi_generation", None)
    if settings is None:
        return ""
    return bpy.path.abspath(settings.python).strip()


def output_directory(context):
    settings = context.scene.remi_texture
    path = bpy.path.abspath(settings.output).strip()
    return Path(path) if path else Path(bpy.utils.user_resource("DATAFILES")) / "remi" / "textures"
