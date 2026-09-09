"""Feature-owned Blender scene setting declarations."""

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)


SCENE_SETTINGS = {
    "remesh_backend": EnumProperty(
            name="Remesh Method",
            description="Choose the fast standard voxel flow or the slower fitted closing-volume reconstruction",
            items=[
                ("VOXEL", "Voxel Remesh", "Fast original Remi SDF voxel remesh"),
                ("VOLUME", "Closing Volume", "Slower high-resolution volume reconstruction that closes holes and fits back to source surfaces and sharp features"),
            ],
            default="VOXEL",
        ),
    "use_sdf_remesh": BoolProperty(
            name="Repair / Remesh",
            description="Enable the SDF remesh stage and its optional hole-preparation step",
            default=True,
        ),
    "voxel_size": FloatProperty(
            name="Voxel Size",
            description="SDF sampling resolution. Smaller values preserve more surface detail",
            default=0.01,
            min=0.001,
            max=0.1,
            precision=4,
            subtype="DISTANCE",
        ),
    "use_sdf_fillet": BoolProperty(
            name="Fillet",
            description="Apply SDF Grid Fillet before Grid to Mesh",
            default=False,
        ),
    "fillet_radius": FloatProperty(
            name="Fillet Radius",
            description="Radius for SDF Grid Fillet (in voxels)",
            default=1.0,
            min=0.0,
            max=10.0,
            precision=2,
        ),
    "use_sdf_smoothing": BoolProperty(
            name="Smooth",
            description="Apply SDF Grid Laplacian smoothing",
            default=False,
        ),
    "smoothing_iterations": IntProperty(
            name="Smoothing Iterations",
            description="Number of SDF Laplacian smoothing iterations",
            default=1,
            min=0,
            max=10,
        ),
    "use_decimation": BoolProperty(
            name="Decimation",
            description="Enable MeshLab decimation step in the pipeline",
            default=True,
        ),
    "decimation_passes": IntProperty(
            name="Decimation Passes",
            description="Number of sequential quadric edge collapse passes",
            default=6,
            min=1,
            max=20,
        ),
    "target_percentage": FloatProperty(
            name="Target % (per pass)",
            description="Percentage of faces to keep in each decimation pass (0.0-1.0)",
            default=0.5,
            min=0.01,
            max=0.99,
            precision=3,
        ),
    "decimation_preserve_detail": BoolProperty(
            name="Preserve Detail",
            description="Use normal preservation and planar quadrics during MeshLab decimation",
            default=True,
        ),
    "decimation_with_texture": BoolProperty(
            name="Keep Texture",
            description=(
                "Preserve the working mesh's UVs and image texture during MeshLab decimation"
            ),
            default=False,
        ),
    "output_name_suffix": StringProperty(
            name="Output Suffix",
            description="Suffix appended to output object name",
            default="_optimized",
        ),
}
