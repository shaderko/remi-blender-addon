"""UV generation service for isolated mesh candidates."""

from __future__ import annotations

from ...blender.mesh_objects import duplicate_object, remove_mesh_object
from .engine import ensure_remi_uv


def _build_candidate(source, settings, suffix="_uv", candidate=None):
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


class UVService:
    """Generate and validate UVs on a session-provided candidate."""

    def generate(self, source, settings, *, candidate=None):
        return _build_candidate(source, settings, candidate=candidate)


def create_candidate(source, settings, suffix="_uv", candidate=None):
    """Compatibility function for standalone callers."""
    return _build_candidate(source, settings, suffix, candidate)
