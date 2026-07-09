"""
Remi - Blender Addon
Automated mesh optimization pipeline:
  SDF Remesh (Geometry Nodes) → MeshLab Decimation → AutoRemesher → Bake Textures
"""

bl_info = {
    "name": "Remi",
    "author": "Remi",
    "version": (1, 0, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Remi",
    "description": "SDF voxel remesh → MeshLab decimation → AutoRemesher → texture baking pipeline",
    "category": "Object",
}

import bpy
from bpy.props import (
    FloatProperty,
    IntProperty,
    BoolProperty,
    StringProperty,
    PointerProperty,
    EnumProperty,
)
from bpy.types import PropertyGroup


# ============================================================
# Property Group (shared settings stored on scene)
# ============================================================
class RemiSceneSettings(PropertyGroup):
    # ── SDF Remesh ────────────────────────────────────────────
    use_sdf_remesh: BoolProperty(
        name="SDF Remesh",
        description="Enable SDF voxel remesh step in the pipeline",
        default=True,
    )
    voxel_size: FloatProperty(
        name="Voxel Size",
        description="Shared value used for both MeshToSDFGrid voxel size "
                    "and GridToMesh threshold. Smaller = higher detail.",
        default=0.02,
        min=0.001,
        max=0.1,
        precision=4,
        subtype="DISTANCE",
    )
    use_sdf_fillet: BoolProperty(
        name="Fillet",
        description="Apply SDF Grid Fillet before Grid to Mesh",
        default=False,
    )
    fillet_radius: FloatProperty(
        name="Fillet Radius",
        description="Radius for SDF Grid Fillet (in voxels)",
        default=1.0,
        min=0.0,
        max=10.0,
        precision=2,
    )
    use_sdf_smoothing: BoolProperty(
        name="Smooth",
        description="Apply SDF Grid Laplacian smoothing",
        default=False,
    )
    smoothing_iterations: IntProperty(
        name="Smoothing Iterations",
        description="Number of SDF Laplacian smoothing iterations",
        default=1,
        min=0,
        max=10,
    )

    # ── MeshLab Decimation ────────────────────────────────────
    use_decimation: BoolProperty(
        name="Decimation",
        description="Enable MeshLab decimation step in the pipeline",
        default=True,
    )
    decimation_passes: IntProperty(
        name="Decimation Passes",
        description="Number of sequential quadric edge collapse passes",
        default=1,
        min=1,
        max=20,
    )
    target_percentage: FloatProperty(
        name="Target % (per pass)",
        description="Percentage of faces to keep in each decimation pass (0.0-1.0)",
        default=0.5,
        min=0.01,
        max=0.99,
        precision=3,
    )
    output_name_suffix: StringProperty(
        name="Output Suffix",
        description="Suffix appended to output object name",
        default="_optimized",
    )

    # ── AutoRemesher (optional external) ──────────────────────
    use_autoremesher: BoolProperty(
        name="AutoRemesher",
        description="Run external AutoRemesher executable for quad-based retopology",
        default=False,
    )
    autoremesher_executable: StringProperty(
        name="Executable",
        description="Path to autoremesher executable (or set AUTOREMESHER_PATH env var)",
        subtype="FILE_PATH",
        default="",
    )
    ar_target_quads: IntProperty(
        name="Target Quads",
        description="Target number of quads for AutoRemesher",
        default=50000,
        min=100,
        soft_max=500000,
    )
    ar_edge_scaling: FloatProperty(
        name="Edge Scaling",
        description="Edge length scaling factor (1.0-4.0)",
        default=1.0,
        min=1.0,
        max=4.0,
        precision=2,
    )
    ar_sharp_edge: FloatProperty(
        name="Sharp Edge",
        description="Dihedral angle threshold in degrees (30-180)",
        default=90.0,
        min=30.0,
        max=180.0,
        precision=1,
    )
    ar_smooth_normal: FloatProperty(
        name="Smooth Normal",
        description="Normal smoothing angle in degrees (0-180)",
        default=0.0,
        min=0.0,
        max=180.0,
        precision=1,
    )
    ar_adaptivity: FloatProperty(
        name="Adaptivity",
        description="Curvature-adaptive quad density (0.0-1.0)",
        default=1.0,
        min=0.0,
        max=1.0,
        precision=2,
    )
    ar_hide_original: BoolProperty(
        name="Hide Original",
        description="Hide the source object after AutoRemesher runs",
        default=False,
    )

    # ── Baking ────────────────────────────────────────────────
    use_baking: BoolProperty(
        name="Bake Textures",
        description="Bake diffuse/roughness/normal textures from original to result",
        default=True,
    )
    bake_texture_size: IntProperty(
        name="Texture Size",
        description="Resolution of baked textures (px, square)",
        default=2048,
        min=256,
        max=8192,
        subtype="PIXEL",
    )
    bake_uv_method: EnumProperty(
        name="UV Method",
        description="Method for generating UVs on the remeshed mesh",
        items=[
            ("SMART", "Smart Project", "Angle-based automatic UV unwrapping"),
            ("LIGHTMAP", "Lightmap Pack", "Dense packing optimal for baking, no seams"),
        ],
        default="SMART",
    )
    bake_uv_island_margin: FloatProperty(
        name="UV Island Margin",
        description="Margin between UV islands as fraction of image (0.0-0.1)",
        default=0.02,
        min=0.0,
        max=0.1,
        precision=3,
    )
    bake_recalc_normals: BoolProperty(
        name="Recalculate Normals",
        description="Recalculate normals on the target mesh before baking (fixes SDF remesh artifacts)",
        default=True,
    )


# ============================================================
# Registration
# ============================================================
classes = [
    RemiSceneSettings,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.remi_settings = PointerProperty(type=RemiSceneSettings)

    from . import operators
    operators.register()

    from . import ui
    ui.register()

    print("Remi: Registered")


def unregister():
    from . import ui
    ui.unregister()

    from . import operators
    operators.unregister()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.remi_settings

    print("Remi: Unregistered")


if __name__ == "__main__":
    register()
