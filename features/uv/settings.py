"""Feature-owned Blender scene setting declarations."""

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)

from .engine.settings import PROFILE_ITEMS


SCENE_SETTINGS = {
    "bake_uv_method": EnumProperty(
            name="UV Method",
            description="Method for generating UVs on the remeshed mesh",
            items=[
                ("REMI", "Remi UV", "Analyzed, seam-aware, distortion-validated automatic UVs"),
                ("SMART", "Smart Project", "Angle-based automatic UV unwrapping"),
                ("LIGHTMAP", "Lightmap Pack", "Dense packing optimal for baking, no seams"),
            ],
            default="REMI",
        ),
    "bake_uv_profile": EnumProperty(
            name="UV Profile",
            description="Coordinated charting, stretch, relaxation, and packing decisions",
            items=PROFILE_ITEMS,
            default="NORMAL_BAKE",
        ),
    "bake_uv_margin_px": IntProperty(
            name="UV Padding",
            description="Padding between Remi UV islands in texture pixels",
            default=4,
            min=0,
            max=256,
            subtype="PIXEL",
        ),
    "bake_uv_preserve_seams": BoolProperty(
            name="Preserve Marked Seams",
            description="Keep artist-marked seam edges as hard chart boundaries",
            default=True,
        ),
    "bake_uv_island_margin": FloatProperty(
            name="UV Island Margin",
            description="Legacy Smart Project/Lightmap margin as a fraction of the image",
            default=0.01,
            min=0.0,
            max=0.1,
            precision=3,
        ),
}
