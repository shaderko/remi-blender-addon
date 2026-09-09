import math

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup


class RemiInstantMeshesSettings(PropertyGroup):
    target_faces: IntProperty(
        name="Target Faces",
        description="Approximate final face count; pure-quad subdivision is accounted for automatically",
        default=20000,
        min=100,
        soft_max=500000,
    )
    pure_quad: BoolProperty(
        name="Pure Quads",
        description="Regularly subdivide the extracted field mesh into pure quads",
        default=True,
    )
    preserve_creases: BoolProperty(
        name="Preserve Creases",
        description="Align the field to source edges sharper than the configured angle",
        default=True,
    )
    crease_angle: FloatProperty(
        name="Crease Angle",
        description="Dihedral angle treated as a sharp feature",
        default=math.radians(35.0),
        min=math.radians(1.0),
        max=math.radians(179.0),
        subtype="ANGLE",
        unit="ROTATION",
    )
    align_boundaries: BoolProperty(
        name="Align Boundaries",
        description="Constrain the field and output grid to open mesh boundaries",
        default=True,
    )
    extrinsic: BoolProperty(
        name="Extrinsic Field",
        description="Optimize directions in 3D instead of only through intrinsic surface transport",
        default=True,
    )
    deterministic: BoolProperty(
        name="Deterministic",
        description="Prefer reproducible but slightly slower hierarchy operations",
        default=False,
    )
    smooth_iterations: IntProperty(
        name="Projection Steps",
        description="Output smoothing and source-surface reprojection passes",
        default=2,
        min=0,
        max=10,
    )
    field_samples: IntProperty(
        name="Field Samples",
        description="Maximum number of orientation crosses displayed in the viewport",
        default=1200,
        min=100,
        max=20000,
    )
    show_orientation: BoolProperty(name="Orientation Field", default=True)
    show_position: BoolProperty(name="Position Field", default=False)
    show_singularities: BoolProperty(name="Singularities", default=True)
    show_preview: BoolProperty(name="Quad Preview", default=True)
    source_dimming: FloatProperty(
        name="Source Dimming",
        description="Darken the original surface so the retopology field and preview remain readable",
        default=0.58,
        min=0.0,
        max=0.95,
        subtype="FACTOR",
    )
    preview_offset: FloatProperty(
        name="Retopo Offset",
        description="Lift the preview away from the source as a fraction of the target quad size",
        default=0.045,
        min=0.0,
        max=0.5,
        precision=3,
        subtype="FACTOR",
    )
    preview_fill_opacity: FloatProperty(
        name="Face Opacity",
        description="Opacity of the retopology face fill behind the preview edges",
        default=0.12,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    preview_xray: BoolProperty(
        name="X-Ray Retopo",
        description="Show preview edges through the source; disable for clean front-surface editing",
        default=False,
    )
    auto_update_preview: BoolProperty(
        name="Auto-update Preview",
        description="Automatically re-extract the quad preview after field solves and guide edits",
        default=True,
    )
    hide_source: BoolProperty(name="Hide Source on Accept", default=False)
    output_suffix: StringProperty(name="Output Suffix", default="_instant")
    status: StringProperty(name="Status", default="Not started")
    progress: FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    session_active: BoolProperty(default=False)


classes = (RemiInstantMeshesSettings,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.remi_instant_meshes = bpy.props.PointerProperty(
        type=RemiInstantMeshesSettings
    )


def unregister():
    if hasattr(bpy.types.Scene, "remi_instant_meshes"):
        del bpy.types.Scene.remi_instant_meshes
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
