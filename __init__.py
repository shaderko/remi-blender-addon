"""
Remi - Blender Addon
Automated mesh optimization pipeline:
  SDF Remesh (Geometry Nodes) → MeshLab Decimation → AutoRemesher → Bake Textures
"""

bl_info = {
    "name": "Remi",
    "author": "Remi",
    "version": (1, 10, 0),
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
    remesh_backend: EnumProperty(
        name="Remesh Method",
        description="Choose the fast standard voxel flow or the slower fitted closing-volume reconstruction",
        items=[
            ("VOXEL", "Voxel Remesh", "Fast original Remi SDF voxel remesh"),
            ("VOLUME", "Closing Volume", "Slower high-resolution volume reconstruction that closes holes and fits back to source surfaces and sharp features"),
        ],
        default="VOXEL",
    )
    use_sdf_remesh: BoolProperty(
        name="Repair / Remesh",
        description="Enable the SDF remesh stage and its optional hole-preparation step",
        default=True,
    )
    use_hole_repair: BoolProperty(
        name="Pre-Repair Holes",
        description="Prepare holes and fragmented cracks before the normal SDF remesh stage",
        default=False,
    )
    hole_repair_method: EnumProperty(
        name="Repair Method",
        description="Choose how damaged open geometry is repaired before remeshing",
        items=[
            ("ALPHA_WRAP", "Alpha-Guided Patches", "Recommended: use CGAL Alpha Wrapping only as a hidden guide, copy its hole-spanning patches onto the original triangles, then continue through SDF remeshing"),
            ("HYBRID", "Hybrid", "Fill boundary loops, then close spatial cracks with an SDF volume"),
            ("BOUNDARY", "Boundary Only", "Triangulate explicit boundary loops without volumetric closing"),
            ("VOLUME", "Volume-Guided Patches", "Recommended: build a high-resolution closing volume as a temporary guide, retain only faces spanning holes, then continue through the normal SDF flow"),
        ],
        default="VOLUME",
    )
    alpha_wrap_alpha_ratio: FloatProperty(
        name="Starting Hole Scale",
        description="Opening and cavity scale followed by the hidden Alpha Wrap guide, relative to the mesh diagonal; raise it when the guide enters holes that should be patched",
        default=0.05,
        min=0.001,
        max=0.25,
        precision=4,
        subtype="FACTOR",
    )
    alpha_wrap_auto_scale: BoolProperty(
        name="Auto Find Hole Scale",
        description="Increase the hidden guide scale until its patches cover enough of the original open boundaries",
        default=True,
    )
    alpha_wrap_max_ratio: FloatProperty(
        name="Maximum Hole Scale",
        description="Largest guide scale Auto Find may use, relative to the object diagonal; increase for extremely fragmented shells",
        default=0.30,
        min=0.01,
        max=0.75,
        precision=3,
        subtype="FACTOR",
    )
    alpha_wrap_coverage_target: FloatProperty(
        name="Boundary Coverage",
        description="Fraction of open boundary samples that should touch generated patches before the guide scale is accepted",
        default=0.85,
        min=0.25,
        max=1.0,
        precision=2,
        subtype="FACTOR",
    )
    alpha_wrap_offset_ratio: FloatProperty(
        name="Surface Offset",
        description="Maximum wrapping offset relative to the mesh diagonal; lower hugs the source more tightly",
        default=0.0015,
        min=0.00005,
        max=0.05,
        precision=5,
        subtype="FACTOR",
    )
    alpha_wrap_patch_ratio: FloatProperty(
        name="Hole Detection",
        description="Minimum distance from the original surface that identifies a guide face as a hole patch, relative to the object diagonal; lower fills smaller gaps",
        default=0.006,
        min=0.0001,
        max=0.1,
        precision=4,
        subtype="FACTOR",
    )
    alpha_wrap_patch_rings: IntProperty(
        name="Border Overlap",
        description="Extra guide-face rings retained around each detected patch so it overlaps the original surface before voxel remeshing",
        default=2,
        min=0,
        max=12,
    )
    alpha_wrap_patch_resolution: FloatProperty(
        name="Patch Resolution",
        description="Target donor-patch edge length as a multiple of the SDF voxel size; lower values create finer patches",
        default=1.0,
        min=0.25,
        max=8.0,
        precision=2,
    )
    alpha_wrap_patch_relax_iterations: IntProperty(
        name="Patch Relaxation",
        description="Smooth only the subdivided patch interiors while locking their borders to the original surface",
        default=6,
        min=0,
        max=30,
    )
    alpha_wrap_executable: StringProperty(
        name="Alpha Wrap Helper",
        description="Optional path to the compiled Remi CGAL Alpha Wrap helper",
        default="",
        subtype="FILE_PATH",
    )
    alpha_wrap_auto_build: BoolProperty(
        name="Build Helper Automatically",
        description="Run CMake automatically when the bundled Alpha Wrap helper has not been built yet",
        default=True,
    )
    hole_max_sides: IntProperty(
        name="Max Boundary Edges",
        description="Maximum edge count of a boundary loop that may be capped; larger openings are left for volumetric closing",
        default=256,
        min=3,
        max=10000,
    )
    hole_weld_distance: FloatProperty(
        name="Weld Distance",
        description="Merge nearly coincident vertices before finding boundary holes; zero disables welding",
        default=0.0,
        min=0.0,
        soft_max=0.01,
        precision=5,
        subtype="DISTANCE",
    )
    hole_close_ratio: FloatProperty(
        name="Crack Size",
        description="Maximum crack scale to close, relative to the mesh bounding-box diagonal",
        default=0.015,
        min=0.0001,
        max=0.2,
        precision=4,
        subtype="FACTOR",
    )
    volume_guide_voxel_scale: FloatProperty(
        name="Guide Resolution",
        description="Volume-guide voxel size as a multiple of the final SDF voxel size; lower values make the temporary guide follow the source more closely",
        default=0.5,
        min=0.1,
        max=2.0,
        precision=2,
    )
    volume_surface_fit_ratio: FloatProperty(
        name="Surface Fit Reach",
        description="Distance within which retained volume-patch vertices are projected exactly onto the original surface, relative to the object diagonal",
        default=0.03,
        min=0.001,
        max=0.25,
        precision=3,
        subtype="FACTOR",
    )
    volume_preserve_features: BoolProperty(
        name="Preserve Sharp Creases",
        description="Detect sharp source edges, including concave inside corners, and fit nearby volume vertices toward those crease lines",
        default=True,
    )
    volume_feature_angle: FloatProperty(
        name="Feature Angle °",
        description="Minimum dihedral angle treated as a sharp crease",
        default=35.0,
        min=5.0,
        max=175.0,
        precision=1,
    )
    volume_feature_reach: FloatProperty(
        name="Crease Fit Reach",
        description="Distance around sharp edges affected by feature fitting, measured in final SDF voxels",
        default=2.5,
        min=0.25,
        max=12.0,
        precision=2,
    )
    targeted_ray_depth_ratio: FloatProperty(
        name="Ray Depth Tolerance",
        description="Allowed front-surface depth variation relative to the object diagonal; lower rejects back-surface hits through the hole",
        default=0.15,
        min=0.01,
        max=1.0,
        precision=3,
        subtype="FACTOR",
    )
    targeted_ray_spacing: IntProperty(
        name="Ray Spacing",
        description="Pixel spacing between ray samples along the drawn stroke; lower values follow the stroke more densely",
        default=8,
        min=2,
        max=40,
    )
    hole_detail_recovery: BoolProperty(
        name="Recover Surface Detail",
        description="Project repaired vertices back toward nearby original surfaces while preserving newly filled gaps",
        default=True,
    )
    hole_detail_ratio: FloatProperty(
        name="Detail Reach",
        description="Maximum distance for detail recovery, relative to the mesh bounding-box diagonal",
        default=0.008,
        min=0.0001,
        max=0.1,
        precision=4,
        subtype="FACTOR",
    )
    voxel_size: FloatProperty(
        name="Voxel Size",
        description="SDF sampling resolution. Smaller values preserve more surface detail",
        default=0.01,
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
        default=6,
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
    decimation_preserve_detail: BoolProperty(
        name="Preserve Detail",
        description="Use normal preservation and planar quadrics during MeshLab decimation",
        default=True,
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
    bake_auto_unwrap: BoolProperty(
        name="Auto Unwrap",
        description="Generate UVs on the bake target when it has no UV map. Disable to use UVs prepared externally",
        default=True,
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
        default=0.01,
        min=0.0,
        max=0.1,
        precision=3,
    )
    bake_recalc_normals: BoolProperty(
        name="Recalculate Normals",
        description="Recalculate normals on the target mesh before baking (fixes SDF remesh artifacts)",
        default=True,
    )
    bake_half_scale: BoolProperty(
        name="Half-Scale Bake",
        description="Temporarily scale both meshes to 50% before baking. "
                    "Improves bake quality when the remesh doesn't perfectly "
                    "align with the original at larger scales.",
        default=True,
    )
    bake_cage_extrusion: FloatProperty(
        name="Cage Extrusion",
        description="Distance to extrude the target surface when casting bake rays. "
                    "Helps rays reach the source when meshes don't perfectly align.",
        default=0.1,
        min=0.0,
        max=10.0,
        precision=3,
        subtype="DISTANCE",
    )
    bake_max_ray_distance: FloatProperty(
        name="Max Ray Distance",
        description="Maximum ray distance for baking. "
                    "Increase if baking misses areas on large meshes.",
        default=0.1,
        min=0.001,
        max=100.0,
        precision=3,
        subtype="DISTANCE",
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

    from . import edit_tools
    edit_tools.register()

    print("Remi: Registered")


def unregister():
    from . import edit_tools
    edit_tools.unregister()

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
