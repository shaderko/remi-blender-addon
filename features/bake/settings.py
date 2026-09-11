"""Feature-owned Blender scene setting declarations."""

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)


SCENE_SETTINGS = {
    "use_baking": BoolProperty(
            name="Bake Textures",
            description="Bake diffuse/roughness/normal textures from original to result",
            default=True,
        ),
    "bake_texture_size": IntProperty(
            name="Texture Size",
            description="Resolution of baked textures (px, square)",
            default=2048,
            min=256,
            max=8192,
            subtype="PIXEL",
        ),
    "bake_auto_unwrap": BoolProperty(
            name="Auto Unwrap",
            description="Generate UVs on the bake target when it has no UV map. Disable to use UVs prepared externally",
            default=True,
        ),
    "bake_recalc_normals": BoolProperty(
            name="Recalculate Normals",
            description="Recalculate normals on the target mesh before baking (fixes SDF remesh artifacts)",
            default=True,
        ),
    "bake_half_scale": BoolProperty(
            name="Half-Scale Bake",
            description="Temporarily scale both meshes to 50% before baking. "
                        "Improves bake quality when the remesh doesn't perfectly "
                        "align with the original at larger scales.",
            default=True,
        ),
    "bake_cage_extrusion": FloatProperty(
            name="Cage Extrusion",
            description="Distance to extrude the target surface when casting bake rays. "
                        "Helps rays reach the source when meshes don't perfectly align.",
            default=0.1,
            min=0.0,
            max=10.0,
            precision=3,
            subtype="DISTANCE",
        ),
    "bake_auto_cage": BoolProperty(
            name="Auto Cage && Ray",
            description="Derive Cage Extrusion and Max Ray Distance from the actual gap "
                        "between the original and the result, instead of fixed world-unit "
                        "values. Uncheck to set both distances by hand",
            default=True,
        ),
    "bake_max_ray_distance": FloatProperty(
            name="Max Ray Distance",
            description="Maximum ray distance for baking. "
                        "Increase if baking misses areas on large meshes.",
            default=0.1,
            min=0.001,
            max=100.0,
            precision=3,
            subtype="DISTANCE",
        ),
}
