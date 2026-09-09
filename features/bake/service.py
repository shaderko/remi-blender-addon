"""Source-to-candidate texture bake service."""

from __future__ import annotations

import bpy

from ... import baking


def _remove_candidate(candidate) -> None:
    if not candidate:
        return
    mesh = candidate.data if candidate.type == "MESH" else None
    bpy.data.objects.remove(candidate, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


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
    """Bake from a disposable source checkpoint onto an isolated candidate."""
    if candidate is None:
        from ...operators import _duplicate_object

        candidate = _duplicate_object(current, suffix)
    try:
        result = baking.bake_textures(
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
        _remove_candidate(candidate)
        raise
    if not result["success"]:
        _remove_candidate(candidate)
        return None, result.get("error", "Baking failed"), result
    return candidate, "", result
