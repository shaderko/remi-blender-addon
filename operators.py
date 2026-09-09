"""Registration facade for Remi's Blender operator adapters."""

import bpy

from .compat.full_pipeline import Remi_OT_FullPipeline
from .features.bake.operators import (
    Remi_OT_BakeAO,
    Remi_OT_BakeAllMaps,
    Remi_OT_BakeDiffuse,
    Remi_OT_BakeNormal,
    Remi_OT_BakeRoughness,
)
from .features.bake.service import create_candidate as _create_bake_candidate
from .features.remesh.decimation import create_candidate as _create_decimate_candidate
from .features.remesh.operators import (
    Remi_OT_ApplyRemesh,
    Remi_OT_Decimate,
    Remi_OT_SDFRemesh,
)
from .features.remesh.service import create_candidate as _create_sdf_candidate
from .features.repair.operators import (
    Remi_OT_BuildAlphaWrap,
    Remi_OT_DrawHolePatch,
    Remi_OT_RepairHoles,
)
from .features.repair.service import (
    _commit_surface_ring_patch,
    _create_repair_candidate,
    _create_surface_ring_patch,
)
from .features.retopology.autoremesher_service import (
    create_candidate as _create_autoremesher_candidate,
)
from .features.retopology.operators import Remi_OT_AutoRemesher
from .features.uv.operators import Remi_OT_GenerateUV
from .features.uv.service import create_candidate as _create_uv_candidate
from .infrastructure.blender.import_glb_operator import Remi_OT_ImportGLB
from .infrastructure.blender.mesh_exchange import (
    export_obj_for_tool as _export_obj_for_tool,
    export_ply as _export_ply,
    import_obj_result as _import_obj_result,
    import_ply as _import_ply,
)
from .infrastructure.blender.mesh_objects import (
    apply_modifiers as _apply_modifiers,
    duplicate_object as _duplicate_object,
    local_bounds_diagonal as _local_bounds_diagonal,
    remove_mesh_object as _remove_mesh_object,
    world_bounds_diagonal as _world_bounds_diagonal,
)


CLASSES = (
    Remi_OT_DrawHolePatch,
    Remi_OT_RepairHoles,
    Remi_OT_BuildAlphaWrap,
    Remi_OT_ImportGLB,
    Remi_OT_SDFRemesh,
    Remi_OT_ApplyRemesh,
    Remi_OT_Decimate,
    Remi_OT_AutoRemesher,
    Remi_OT_GenerateUV,
    Remi_OT_BakeAllMaps,
    Remi_OT_BakeDiffuse,
    Remi_OT_BakeRoughness,
    Remi_OT_BakeNormal,
    Remi_OT_BakeAO,
    Remi_OT_FullPipeline,
)


def register():
    for operator_class in CLASSES:
        bpy.utils.register_class(operator_class)


def unregister():
    for operator_class in reversed(CLASSES):
        bpy.utils.unregister_class(operator_class)
