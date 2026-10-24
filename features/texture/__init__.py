"""Texture workspace lifecycle, separate from the mesh-editing session."""
import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

from .operators import (
    Remi_OT_CancelProjection,
    Remi_OT_ProjectSplats,
    Remi_OT_TexturePreview,
    runtime,
)
from .settings import RemiTextureSettings, RemiTextureState


CLASSES = (
    RemiTextureSettings,
    RemiTextureState,
    Remi_OT_ProjectSplats,
    Remi_OT_CancelProjection,
    Remi_OT_TexturePreview,
)


@persistent
def _before_load(_unused):
    runtime.stop()


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.remi_texture = PointerProperty(type=RemiTextureSettings)
    bpy.types.WindowManager.remi_texture = PointerProperty(type=RemiTextureState)
    bpy.app.handlers.load_pre.append(_before_load)


def unregister():
    runtime.stop()
    if _before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_before_load)
    del bpy.types.WindowManager.remi_texture
    del bpy.types.Scene.remi_texture
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
