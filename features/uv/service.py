"""UV generation service for isolated mesh candidates."""

from __future__ import annotations

from ...infrastructure.blender.mesh_objects import duplicate_object, remove_mesh_object
from ...uv_mapping import ensure_remi_uv


def create_candidate(source, settings, suffix="_uv", candidate=None):
    """Generate validated UVs on a separate candidate object."""
    if candidate is None:
        candidate = duplicate_object(source, suffix)
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
        remove_mesh_object(candidate)
        raise
    if not result.success:
        remove_mesh_object(candidate)
        return None, result.error or "Remi UV generation failed", {}
    return candidate, "", {
        "chart_count": result.chart_count,
        "stats": result.stats,
        "warnings": list(result.warnings),
    }
