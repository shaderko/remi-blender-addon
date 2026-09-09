"""Register Blender types contributed by the composed workflow features."""

import bpy

from ..app.application import get_application


_registered_classes = []


def register():
    try:
        for feature in get_application().features:
            for blender_class in feature.blender_classes():
                bpy.utils.register_class(blender_class)
                _registered_classes.append(blender_class)
    except Exception:
        unregister()
        raise


def unregister():
    for blender_class in reversed(_registered_classes):
        bpy.utils.unregister_class(blender_class)
    _registered_classes.clear()
