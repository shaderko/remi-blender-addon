"""Feature-owned Blender scene setting declarations."""

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)


SCENE_SETTINGS = {
    "use_autoremesher": BoolProperty(
            name="AutoRemesher",
            description="Run external AutoRemesher executable for quad-based retopology",
            default=False,
        ),
    "autoremesher_executable": StringProperty(
            name="Executable",
            description="Path to autoremesher executable (or set AUTOREMESHER_PATH env var)",
            subtype="FILE_PATH",
            default="",
        ),
    "ar_target_quads": IntProperty(
            name="Target Quads",
            description="Target number of quads for AutoRemesher",
            default=50000,
            min=100,
            soft_max=500000,
        ),
    "ar_edge_scaling": FloatProperty(
            name="Edge Scaling",
            description="Edge length scaling factor (1.0-4.0)",
            default=1.0,
            min=1.0,
            max=4.0,
            precision=2,
        ),
    "ar_sharp_edge": FloatProperty(
            name="Sharp Edge",
            description="Dihedral angle threshold in degrees (30-180)",
            default=90.0,
            min=30.0,
            max=180.0,
            precision=1,
        ),
    "ar_smooth_normal": FloatProperty(
            name="Smooth Normal",
            description="Normal smoothing angle in degrees (0-180)",
            default=0.0,
            min=0.0,
            max=180.0,
            precision=1,
        ),
    "ar_adaptivity": FloatProperty(
            name="Adaptivity",
            description="Curvature-adaptive quad density (0.0-1.0)",
            default=1.0,
            min=0.0,
            max=1.0,
            precision=2,
        ),
    "ar_hide_original": BoolProperty(
            name="Hide Original",
            description="Hide the source object after AutoRemesher runs",
            default=False,
        ),
}
