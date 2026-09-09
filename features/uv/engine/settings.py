"""Profiles and public settings for Remi's automatic UV pipeline."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UVProfile:
    """A small set of coordinated UV decisions exposed as one artist preset."""

    identifier: str
    label: str
    description: str
    seam_angle_degrees: float
    cluster_angle_degrees: float
    stretch_limit: float
    solver: str = "MINIMUM_STRETCH"
    iterations: int = 18
    repair_passes: int = 1
    preserve_material_boundaries: bool = True
    preserve_sharp_edges: bool = True
    use_directional_charts: bool = True
    rotate_method: str = "AXIS_ALIGNED"


PROFILES = {
    "BALANCED": UVProfile(
        "BALANCED",
        "Game Asset — Balanced",
        "Structure-aware charts, moderate stretch, uniform scale, and mip-safe packing",
        seam_angle_degrees=58.0,
        cluster_angle_degrees=32.0,
        stretch_limit=4.0,
        iterations=18,
    ),
    "TEXTURE_PAINT": UVProfile(
        "TEXTURE_PAINT",
        "Texture Painting",
        "Fewer, larger charts with stronger continuity for painting",
        seam_angle_degrees=76.0,
        cluster_angle_degrees=44.0,
        stretch_limit=5.0,
        iterations=24,
        rotate_method="AXIS_ALIGNED",
    ),
    "NORMAL_BAKE": UVProfile(
        "NORMAL_BAKE",
        "Normal Bake",
        "Strict stretch control, no overlap, and extra solver refinement",
        seam_angle_degrees=52.0,
        cluster_angle_degrees=28.0,
        stretch_limit=3.0,
        iterations=30,
        repair_passes=2,
    ),
    "LIGHTMAP": UVProfile(
        "LIGHTMAP",
        "Lightmap",
        "Low-distortion non-overlapping charts with conservative boundaries",
        seam_angle_degrees=42.0,
        cluster_angle_degrees=24.0,
        stretch_limit=2.6,
        iterations=24,
        repair_passes=2,
        rotate_method="CARDINAL",
    ),
    "HARD_SURFACE": UVProfile(
        "HARD_SURFACE",
        "Hard Surface",
        "Planar and directional charts split along creases and material changes",
        seam_angle_degrees=38.0,
        cluster_angle_degrees=20.0,
        stretch_limit=3.2,
        iterations=14,
        rotate_method="AXIS_ALIGNED",
    ),
    "ORGANIC": UVProfile(
        "ORGANIC",
        "Organic",
        "Larger pelt-like charts with stronger minimum-stretch relaxation",
        seam_angle_degrees=82.0,
        cluster_angle_degrees=48.0,
        stretch_limit=5.0,
        iterations=30,
        repair_passes=2,
        preserve_material_boundaries=False,
        rotate_method="ANY",
    ),
    "SCAN": UVProfile(
        "SCAN",
        "Scan / AI Mesh",
        "Robust directional charting for noisy, irregular triangulated meshes",
        seam_angle_degrees=64.0,
        cluster_angle_degrees=25.0,
        stretch_limit=3.5,
        iterations=20,
        repair_passes=2,
        rotate_method="ANY",
    ),
}


PROFILE_ITEMS = tuple(
    (profile.identifier, profile.label, profile.description)
    for profile in PROFILES.values()
)


def get_profile(identifier: str) -> UVProfile:
    """Return a known profile, falling back to the general-purpose preset."""
    return PROFILES.get(identifier, PROFILES["BALANCED"])
