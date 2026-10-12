"""Compatibility-preserving composition of feature-owned scene settings."""

import bpy
from bpy.props import PointerProperty
from bpy.types import PropertyGroup

from ..app.application import get_application


class RemiSceneSettings(PropertyGroup):
    """Stable scene.remi_settings facade assembled from injected features."""


def compose_scene_settings(features):
    settings = {}
    owners = {}
    for feature in features:
        feature_id = feature.descriptor.id
        for name, definition in feature.scene_settings().items():
            if name in settings:
                raise ValueError(
                    f"Remi scene setting {name} is owned by both "
                    f"{owners[name]} and {feature_id}"
                )
            settings[name] = definition
            owners[name] = feature_id
    return settings


def register():
    RemiSceneSettings.__annotations__ = compose_scene_settings(
        get_application().features
    )
    bpy.utils.register_class(RemiSceneSettings)
    bpy.types.Scene.remi_settings = PointerProperty(type=RemiSceneSettings)


def unregister():
    if hasattr(bpy.types.Scene, "remi_settings"):
        del bpy.types.Scene.remi_settings
    bpy.utils.unregister_class(RemiSceneSettings)
