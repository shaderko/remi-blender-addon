"""Source generation lifecycle, intentionally outside mesh workflow stages."""
import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

from .operators import (
    Remi_OT_GenerateSplats,
    Remi_OT_CancelGeneration,
    Remi_OT_ImportSplats,
    Remi_OT_ActivateSplat,
    runtime,
)
from .mesh_operators import Remi_OT_CreateMesh, Remi_OT_CancelMesh, Remi_OT_InstallSurfaceTool, runtime as mesh_runtime
from .model_operators import Remi_OT_DownloadModels
from .panel import Remi_PT_GenerationSettings
from .settings import RemiGenerationSettings, RemiGenerationState
from ...integrations.triposplat.client import shutdown_generation_server


CLASSES = (RemiGenerationSettings, RemiGenerationState, Remi_OT_GenerateSplats,
           Remi_OT_CancelGeneration, Remi_OT_ImportSplats, Remi_OT_ActivateSplat,
           Remi_OT_CreateMesh,
            Remi_OT_CancelMesh, Remi_OT_InstallSurfaceTool, Remi_OT_DownloadModels, Remi_PT_GenerationSettings)


@persistent
def _before_load(_unused):
    runtime.stop()
    mesh_runtime.stop()


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.remi_generation = PointerProperty(type=RemiGenerationSettings)
    bpy.types.WindowManager.remi_generation = PointerProperty(type=RemiGenerationState)
    bpy.app.handlers.load_pre.append(_before_load)


def unregister():
    runtime.stop()
    mesh_runtime.stop()
    shutdown_generation_server()
    if _before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_before_load)
    del bpy.types.WindowManager.remi_generation
    del bpy.types.Scene.remi_generation
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
