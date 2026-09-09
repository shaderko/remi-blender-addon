"""Registration for non-session operator IDs retained for compatibility."""

import bpy

from .full_pipeline import Remi_OT_FullPipeline
from ..blender.import_glb_operator import Remi_OT_ImportGLB


CLASSES = (Remi_OT_ImportGLB, Remi_OT_FullPipeline)
_registered_classes = []


def register():
    try:
        for blender_class in CLASSES:
            bpy.utils.register_class(blender_class)
            _registered_classes.append(blender_class)
    except Exception:
        unregister()
        raise


def unregister():
    for blender_class in reversed(_registered_classes):
        bpy.utils.unregister_class(blender_class)
    _registered_classes.clear()
