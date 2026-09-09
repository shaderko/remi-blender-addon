"""Standalone GLB import operator retained for compatibility."""

import os

import bpy
from bpy.types import Operator


class Remi_OT_ImportGLB(Operator):
    """Import a GLB file into the scene."""
    bl_idname = "remi.import_glb"
    bl_label = "Import GLB"
    bl_description = "Import a GLB/glTF file into the scene"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")  # type: ignore

    def execute(self, context):
        settings = context.scene.remi_settings

        if self.filepath:
            filepath = self.filepath
        elif settings.import_glb_path:
            filepath = settings.import_glb_path
        else:
            self.report({"ERROR"}, "No GLB file specified")
            return {"CANCELLED"}

        if not os.path.exists(filepath):
            self.report({"ERROR"}, f"File not found: {filepath}")
            return {"CANCELLED"}

        # Import GLB/glTF
        prev_objects = set(bpy.context.scene.objects)
        try:
            bpy.ops.import_scene.gltf(filepath=filepath)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to import GLB: {e}")
            return {"CANCELLED"}

        # Find imported objects
        new_objs = [o for o in bpy.context.scene.objects if o not in prev_objects]
        if not new_objs:
            self.report({"WARNING"}, "No objects imported (file may be empty)")
            return {"CANCELLED"}

        # Select the first imported mesh
        for obj in new_objs:
            if obj.type == "MESH":
                bpy.context.view_layer.objects.active = obj
                obj.select_set(True)
                break

        self.report({"INFO"}, f"Imported {len(new_objs)} object(s) from GLB")
        return {"FINISHED"}

    def invoke(self, context, event):
        settings = context.scene.remi_settings
        if settings.import_glb_path:
            self.filepath = settings.import_glb_path
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}
