"""Blender operator adapter for UV generation."""

import bpy
from bpy.types import Operator

from .service import create_candidate as _create_uv_candidate


class Remi_OT_GenerateUV(Operator):
    """Generate a validated Remi UV map for the active mesh."""

    bl_idname = "remi.generate_uv"
    bl_label = "Generate Remi UV"
    bl_description = "Analyze the active mesh, generate geometry-aware charts, unwrap, validate, and pack its UVs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(
            context.mode in {"OBJECT", "EDIT_MESH"}
            and context.active_object
            and context.active_object.type == "MESH"
        )

    def execute(self, context):
        obj = context.active_object
        settings = context.scene.remi_settings
        candidate, error, report = _create_uv_candidate(obj, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        # Standalone UV keeps its historical in-place behavior while sharing
        # the same isolated candidate path as a Remi session.
        old_mesh = obj.data
        obj.data = candidate.data
        bpy.data.objects.remove(candidate, do_unlink=True)
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
        stats = report["stats"]
        if stats:
            self.report(
                {"INFO"},
                f"Remi UV: {report['chart_count']} charts, "
                f"p95 stretch {stats.conformal_p95:.2f}, "
                f"{stats.packing_occupancy:.0%} occupancy",
            )
        else:
            self.report({"INFO"}, "Remi UV map is ready")
        if report["warnings"]:
            print("Remi UV warnings: " + "; ".join(report["warnings"]))
        return {"FINISHED"}
