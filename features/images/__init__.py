"""Local Iris-3B image generation workspace."""

import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

from .gallery import gallery
from .operators import classes as operator_classes, runtime
from .panel import classes as panel_classes
from .settings import RemiImageSettings, RemiImageState
from ...integrations.iris3b.client import shutdown_iris_server

CLASSES = (RemiImageSettings, RemiImageState, *panel_classes, *operator_classes)


@persistent
def _before_load(_unused):
    runtime.stop()
    gallery.close()


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.remi_image = PointerProperty(type=RemiImageSettings)
    bpy.types.WindowManager.remi_image = PointerProperty(type=RemiImageState)
    bpy.app.handlers.load_pre.append(_before_load)


def unregister():
    runtime.stop()
    shutdown_iris_server()
    gallery.close()
    if _before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_before_load)
    del bpy.types.WindowManager.remi_image
    del bpy.types.Scene.remi_image
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
