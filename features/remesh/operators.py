"""Blender operator adapters for remesh and decimation."""

import bpy
from bpy.types import Operator

from ...blender.mesh_objects import apply_modifiers as _apply_modifiers
from .decimation import create_candidate as _create_decimate_candidate
from . import geometry_nodes
from .service import create_candidate as _create_sdf_candidate


class Remi_OT_SDFRemesh(Operator):
    """Apply SDF voxel remesh to selected object (via geometry nodes on a copy)."""
    bl_idname = "remi.sdf_remesh"
    bl_label = "SDF Voxel Remesh"
    bl_description = "Duplicate selected object and apply SDF grid remesh via Geometry Nodes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings

        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        result, error, report = _create_sdf_candidate(obj, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        if settings.remesh_backend == "VOLUME":
            self.report(
                {"INFO"},
                f"Closing Volume created {report['faces']:,} faces; fitted "
                f"{report['surface_projected']:,} surface and {report['feature_fitted']:,} crease vertices",
            )
            return {"FINISHED"}

        if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
            if report.get("guide_method") == "VOLUME":
                self.report({"INFO"}, f"Added {report['patch_faces']:,} fitted volume-patch faces")
            else:
                self.report(
                    {"INFO"},
                    f"Added {report['patch_faces']:,} hole-patch faces "
                    f"({report['boundary_coverage']:.0%} boundary coverage)",
                )
        self.report({"INFO"}, f"Applied SDF remesh to '{result.name}'")
        return {"FINISHED"}


class Remi_OT_ApplyRemesh(Operator):
    """Apply the SDF remesh modifier, converting it to real geometry."""
    bl_idname = "remi.apply_remesh"
    bl_label = "Apply Remesh"
    bl_description = "Apply the geometry nodes modifier to bake the remeshed geometry"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        # Find and apply AR modifier
        group = geometry_nodes.ensure_remi_node_group()
        found = False
        for mod in obj.modifiers:
            is_remi_group = (
                mod.type == "NODES"
                and mod.node_group
                and (
                    mod.node_group == group
                    or mod.node_group.name.startswith("Remi_SDF_Remesh")
                )
            )
            if is_remi_group:
                _apply_modifiers(obj)
                found = True
                break

        if not found:
            self.report({"ERROR"}, "No AR_SDF_Remesh modifier found on active object")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Applied remesh on '{obj.name}'")
        return {"FINISHED"}


class Remi_OT_Decimate(Operator):
    """Run standalone PyMeshLab decimation on the active mesh."""
    bl_idname = "remi.decimate"
    bl_label = "Decimate (MeshLab)"
    bl_description = "Run MeshLab quadric edge collapse on the active object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}
        candidate, error, results = _create_decimate_candidate(obj, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        for result in results:
            print(
                f"Remi: Pass {result['pass']}: "
                f"{result.get('input_faces', '?')} → {result.get('output_faces', '?')} faces"
            )
        bpy.ops.object.select_all(action="DESELECT")
        candidate.select_set(True)
        context.view_layer.objects.active = candidate
        self.report({"INFO"}, f"Decimated model imported as '{candidate.name}'")
        return {"FINISHED"}
