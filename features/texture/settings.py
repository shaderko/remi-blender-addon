"""Scene and runtime properties for the Texture workspace."""
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from ...blender.gaussian_splats import is_splat_object


def _default_output():
    return str(Path(bpy.utils.user_resource("DATAFILES")) / "remi" / "textures")


def _splat_poll(_self, obj):
    return is_splat_object(obj)


class RemiTextureSettings(PropertyGroup):
    source_object: PointerProperty(
        name="Splat Source",
        description="Gaussian splat that supplies the projected appearance; leave empty to use the mesh's linked splat",
        type=bpy.types.Object,
        poll=_splat_poll,
    )
    texture_size: EnumProperty(
        name="Texture",
        description="Projected texture resolution",
        items=[
            ("1024", "1K", "1024 × 1024"),
            ("2048", "2K", "2048 × 2048"),
            ("4096", "4K", "4096 × 4096"),
        ],
        default="2048",
    )
    samples: IntProperty(
        name="Samples",
        description="Nearest Gaussians evaluated per texel",
        default=16,
        min=4,
        max=32,
    )
    normal_map_mode: EnumProperty(
        name="Normal Map",
        description="Choose between the splat-derived normal map and an existing baked normal map",
        items=[
            ("PROJECTED", "Projected", "Use the normal map derived from the Gaussian splat field"),
            ("EXISTING", "Keep Baked", "Keep the normal map already on the mesh, for example from Bake Normal"),
        ],
        default="PROJECTED",
    )
    normal_source: EnumProperty(
        name="Normals",
        description="How the projected normal map is derived from the splat field",
        items=[
            ("AUTO", "Auto", "Use covariance normals for flat, agreeing splats; density gradient for moderate anisotropy; clean mesh normals otherwise"),
            ("GRADIENT", "Density Gradient", "Gradient of the splat density field"),
            ("COVARIANCE", "Splat Axis", "Shortest covariance axis of nearby Gaussians"),
            ("MESH", "Clean Mesh", "Use the cleaned mesh normals only"),
        ],
        default="AUTO",
    )
    normal_strength: FloatProperty(
        name="Normal Strength",
        description="Blend between the clean mesh normal and the projected splat normal",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    normal_smoothing: IntProperty(
        name="Normal Smoothing",
        description="Texture-space smoothing radius for splat normals in pixels; suppresses per-texel jitter",
        default=2,
        min=0,
        max=8,
        subtype="PIXEL",
    )
    smooth_mesh: BoolProperty(
        name="Smooth Mesh",
        description="Set the target to smooth shading so projected normals encode splat detail instead of facet edges",
        default=True,
    )
    fill_holes: BoolProperty(
        name="Fill Holes",
        description="Fill low-coverage texels from the nearest valid projected texel",
        default=True,
    )
    fill_background: BoolProperty(
        name="Fill Background",
        description="Dilate projected colour into the entire texture background, including UV poles and island gaps",
        default=True,
    )
    margin_px: IntProperty(
        name="Margin",
        description="Pixels of seam padding around UV islands",
        default=8,
        min=0,
        max=64,
        subtype="PIXEL",
    )
    pack_images: BoolProperty(
        name="Pack Images",
        description="Pack projected textures into the .blend instead of leaving them external",
        default=True,
    )
    output: StringProperty(
        name="Output",
        description="Folder for projected texture runs",
        subtype="DIR_PATH",
        default=_default_output(),
    )


class RemiTextureState(PropertyGroup):
    busy: BoolProperty(default=False, options={"SKIP_SAVE"})
    status: StringProperty(default="", options={"SKIP_SAVE"})
    status_kind: EnumProperty(
        items=[(key, key.title(), "") for key in ("IDLE", "PROGRESS", "SUCCESS", "ERROR", "CANCELLED")],
        default="IDLE",
        options={"SKIP_SAVE"},
    )
    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
    mesh_name: StringProperty(default="", options={"SKIP_SAVE"})
    source_name: StringProperty(default="", options={"SKIP_SAVE"})
