"""Generation is a source creator, separate from mesh-session settings."""
import os
import json
from pathlib import Path
import shutil

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup


def _configuration_defaults():
    """Optional per-Blender-profile paths, without changing startup.blend."""
    path = Path(bpy.utils.user_resource("CONFIG")) / "remi-generation.json"
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(values, dict):
        return {}
    return {key: value for key, value in values.items()
            if key in {"python", "models", "output", "mesh_output"} and isinstance(value, str) and value.strip()}


_LOCAL_DEFAULTS = _configuration_defaults()


def default_python():
    return _LOCAL_DEFAULTS.get("python") or shutil.which("python") or ""


def _models_default():
    local = Path(__file__).resolve().parents[2] / "models" / "triposplat"
    return os.environ.get("REMI_TRIPOSPLAT_MODELS") or _LOCAL_DEFAULTS.get("models") or str(
        local if local.is_dir() else Path(bpy.utils.user_resource("DATAFILES")) / "remi" / "models" / "triposplat"
    )


class RemiGenerationSettings(PropertyGroup):
    image: StringProperty(name="Image", subtype="FILE_PATH")
    python: StringProperty(
        name="Python", subtype="FILE_PATH", default=default_python(),
        description="Detected automatically; leave empty to use the local default, or choose a CUDA/PyTorch Python environment",
    )
    models: StringProperty(name="Model Folder", subtype="DIR_PATH", default=_models_default())
    output: StringProperty(
        name="Output Folder", subtype="DIR_PATH",
        default=_LOCAL_DEFAULTS.get("output", str(Path(bpy.utils.user_resource("DATAFILES")) / "remi" / "generations")),
    )
    mesh_output: StringProperty(
        name="Mesh Output", subtype="DIR_PATH",
        default=_LOCAL_DEFAULTS.get("mesh_output", str(Path(bpy.utils.user_resource("DATAFILES")) / "remi" / "meshes")),
    )
    mesh_detail: EnumProperty(name="Mesh Detail", items=[
        ("160", "Draft", "About 470k triangles; fastest"),
        ("224", "Standard", "About 1M triangles; balanced"),
        ("288", "High", "About 1.6M triangles; finest"),
    ], default="224")
    mesh_smoothing: EnumProperty(name="Smoothing", items=[
        ("6", "Crisp", "Less smoothing; keeps more surface noise"),
        ("12", "Balanced", "Recommended"),
        ("20", "Smooth", "Smoother, may soften fine details"),
    ], default="12")
    gaussians: EnumProperty(name="Gaussians", items=[
        (str(n), f"{n:,}", "Number of native Gaussian splats") for n in (32768, 65536, 131072, 262144)
    ], default="65536")
    seed: IntProperty(name="Seed", default=20261023, min=0, max=2147483647)
    preview_limit: IntProperty(
        name="Preview Limit", default=32768, min=1, max=262144,
        description="Maximum ellipsoid proxies for new imports; all native Gaussians are retained",
    )
    # Retained for older saved scenes; runtime controls now live in the gear popover.
    show_setup: BoolProperty(name="Local Runtime Setup", default=True, options={"HIDDEN"})


class RemiGenerationState(PropertyGroup):
    busy: BoolProperty(default=False, options={"SKIP_SAVE"})
    status: StringProperty(default="", options={"SKIP_SAVE"})
    status_kind: EnumProperty(items=[(key, key.title(), "") for key in
                                   ("IDLE", "PROGRESS", "SUCCESS", "ERROR", "CANCELLED")],
                              default="IDLE", options={"SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
    mesh_busy: BoolProperty(default=False, options={"SKIP_SAVE"})
    mesh_status: StringProperty(default="", options={"SKIP_SAVE"})
    mesh_status_kind: EnumProperty(items=[(key, key.title(), "") for key in
                                        ("IDLE", "PROGRESS", "SUCCESS", "ERROR", "CANCELLED")],
                                   default="IDLE", options={"SKIP_SAVE"})
    mesh_directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
    mesh_source_object: StringProperty(default="", options={"SKIP_SAVE"})
