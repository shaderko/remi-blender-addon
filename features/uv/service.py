"""UV generation service for isolated mesh candidates."""

from __future__ import annotations

import bpy

from ...uv_mapping import ensure_remi_uv


def _remove_candidate(candidate) -> None:
    if not candidate:
        return
    mesh = candidate.data if candidate.type == "MESH" else None
    bpy.data.objects.remove(candidate, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def create_candidate(source, settings, suffix="_uv", candidate=None):
    """Generate validated UVs on a separate candidate object."""
    if candidate is None:
        from ...operators import _duplicate_object

        candidate = _duplicate_object(source, suffix)
    try:
        result = ensure_remi_uv(
            candidate,
            profile_id=settings.bake_uv_profile,
            texture_size=settings.bake_texture_size,
            margin_px=settings.bake_uv_margin_px,
            preserve_existing_seams=settings.bake_uv_preserve_seams,
            replace_existing=False,
            trust_stored_result=True,
        )
    except Exception:
        _remove_candidate(candidate)
        raise
    if not result.success:
        _remove_candidate(candidate)
        return None, result.error or "Remi UV generation failed", {}
    return candidate, "", {
        "chart_count": result.chart_count,
        "stats": result.stats,
        "warnings": list(result.warnings),
    }
