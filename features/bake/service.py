"""Source-to-candidate texture bake service."""

from __future__ import annotations

from ...blender.mesh_objects import duplicate_object, remove_mesh_object
from . import engine


def _build_candidate(
    source_checkpoint,
    current,
    settings,
    *,
    passes=("diffuse", "roughness", "normal", "ao"),
    name_prefix="",
    suffix="_baked",
    candidate=None,
):
    """Bake from a disposable source checkpoint onto an isolated candidate."""
    if candidate is None:
        candidate = duplicate_object(current, suffix)
    try:
        result = engine.bake_textures(
            source_checkpoint,
            candidate,
            texture_size=settings.bake_texture_size,
            final_name=name_prefix or current.name,
            uv_method=settings.bake_uv_method,
            uv_island_margin=settings.bake_uv_island_margin,
            uv_profile=settings.bake_uv_profile,
            uv_margin_px=settings.bake_uv_margin_px,
            uv_preserve_seams=settings.bake_uv_preserve_seams,
            auto_unwrap=settings.bake_auto_unwrap,
            recalc_normals=settings.bake_recalc_normals,
            cage_extrusion=settings.bake_cage_extrusion,
            max_ray_distance=settings.bake_max_ray_distance,
            passes=passes,
            consume_sources=True,
            reuse_outputs=False,
        )
    except Exception:
        remove_mesh_object(candidate)
        raise
    if not result["success"]:
        remove_mesh_object(candidate)
        return None, result.get("error", "Baking failed"), result
    return candidate, "", result


class BakeService:
    """Bake source-checkpoint appearance onto a session candidate."""

    def bake(
        self,
        source_checkpoint,
        current,
        settings,
        *,
        passes,
        name_prefix,
        candidate=None,
    ):
        return _build_candidate(
            source_checkpoint,
            current,
            settings,
            passes=passes,
            name_prefix=name_prefix,
            candidate=candidate,
        )


def create_candidate(
    source_checkpoint,
    current,
    settings,
    *,
    passes=("diffuse", "roughness", "normal", "ao"),
    name_prefix="",
    suffix="_baked",
    candidate=None,
):
    """Compatibility function for standalone callers."""
    return _build_candidate(
        source_checkpoint,
        current,
        settings,
        passes=passes,
        name_prefix=name_prefix,
        suffix=suffix,
        candidate=candidate,
    )
