"""Image-generation settings live beside, not inside, mesh workflow state."""

import os
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

from ...integrations.iris3b.constants import SIZE_BUCKETS


def _default_root():
    return Path(bpy.utils.user_resource("DATAFILES")) / "remi"


def _models_default():
    return os.environ.get("REMI_IRIS_MODELS") or str(_default_root() / "models")


def _output_default():
    return str(_default_root() / "images")


class RemiImageSettings(PropertyGroup):
    prompt: StringProperty(
        name="Prompt",
        description="Describe the image; plain descriptive English works best",
        default="",
    )
    negative_prompt: StringProperty(
        name="Negative",
        description="What should be avoided (Iris-3B base only; Turbo has no CFG)",
        default="",
    )
    model: EnumProperty(
        name="Model",
        items=[
            ("turbo", "Turbo", "Iris-3B Turbo: 4 steps, about 6 GB, best for iteration"),
            ("base", "Base · Quality", "Iris-3B base: 100 steps, about 12 GB, more text and variety"),
        ],
        default="turbo",
    )
    size: EnumProperty(
        name="Size",
        items=[
            (key, f"{width} × {height}", "Native Iris resolution bucket")
            for key, (width, height) in SIZE_BUCKETS.items()
        ],
        default="SQUARE",
    )
    steps: IntProperty(name="Steps", default=100, min=10, max=150)
    cfg_scale: IntProperty(name="CFG", default=3, min=1, max=10)
    seed: IntProperty(name="Seed", default=0, min=0, max=2147483647)
    count: IntProperty(name="Images", default=1, min=1, max=8)
    models: StringProperty(name="Model Folder", subtype="DIR_PATH", default=_models_default())
    output: StringProperty(name="Output Folder", subtype="DIR_PATH", default=_output_default())
    memory_mode: EnumProperty(
        name="GPU Memory",
        items=[
            ("auto", "Auto", "Keep both networks on the GPU when it fits; otherwise offload one at a time"),
            ("low", "Low VRAM", "Use bfloat16 weights and move the text encoder/model on demand (slower)"),
            ("high", "Full GPU", "Keep the text encoder and model resident on the GPU (needs about 28 GB)"),
        ],
        default="auto",
    )
    cache_minutes: IntProperty(
        name="Keep Model Loaded",
        description="Minutes to keep the external Iris worker warm between generations; 0 reloads every run",
        default=5,
        min=0,
        max=120,
    )
    show_settings: BoolProperty(name="Runtime Settings", default=False)


class RemiImageState(PropertyGroup):
    busy: BoolProperty(default=False, options={"SKIP_SAVE"})
    status: StringProperty(default="", options={"SKIP_SAVE"})
    status_kind: EnumProperty(
        items=[(key, key.title(), "") for key in ("IDLE", "PROGRESS", "SUCCESS", "ERROR", "CANCELLED")],
        default="IDLE",
        options={"SKIP_SAVE"},
    )
    progress: FloatProperty(default=0.0, min=0.0, max=1.0, options={"SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
    selected: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    page: IntProperty(default=0, min=0, options={"SKIP_SAVE"})
    refresh_token: IntProperty(default=0, options={"SKIP_SAVE"})


def dimensions(settings):
    return SIZE_BUCKETS[settings.size]
