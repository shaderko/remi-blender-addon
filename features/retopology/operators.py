"""Blender operator adapters for automatic retopology."""

import bpy
from bpy.types import Operator

from .autoremesher_service import create_candidate as _create_autoremesher_candidate


class Remi_OT_AutoRemesher(Operator):
    """Run AutoRemesher external tool on the active mesh."""
    bl_idname = "remi.autoremesher"
    bl_label = "AutoRemesher (External)"
    bl_description = "Run the external AutoRemesher executable on the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        candidate, error, _report = _create_autoremesher_candidate(obj, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        if settings.ar_hide_original:
            obj.hide_set(True)
        bpy.ops.object.select_all(action="DESELECT")
        candidate.select_set(True)
        context.view_layer.objects.active = candidate
        self.report({"INFO"}, f"AutoRemesher result imported as '{candidate.name}'")
        return {"FINISHED"}
