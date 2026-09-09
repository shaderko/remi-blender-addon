"""Blender operator adapters for standalone multi-object baking."""

from bpy.types import Operator

from ... import baking


class Remi_BakeOperatorMixin:
    """Bake albedo, roughness, normal, and AO maps onto the active mesh."""
    bake_passes = ("diffuse", "roughness", "normal", "ao")

    @classmethod
    def poll(cls, context):
        # Keep the buttons available; execute() gives a useful selection hint
        # when a source mesh has not been selected yet.
        return bool(
            context.mode == "OBJECT"
            and context.active_object
            and context.active_object.type == "MESH"
        )

    def execute(self, context):
        # The ACTIVE object receives the bake (= the remeshed/optimized mesh)
        # The other SELECTED objects provide the detail (= the original meshes)
        target = context.active_object
        sources = [o for o in context.selected_objects if o != target and o.type == "MESH"]
        if not sources:
            self.report({"ERROR"}, "Select original mesh(es) first, then Shift-select the target "
                                     "(optimized mesh) last so it becomes active")
            return {"CANCELLED"}

        s = context.scene.remi_settings
        result = baking.bake_textures(
            sources, target,
            texture_size=s.bake_texture_size,
            uv_method=s.bake_uv_method,
            uv_island_margin=s.bake_uv_island_margin,
            uv_profile=s.bake_uv_profile,
            uv_margin_px=s.bake_uv_margin_px,
            uv_preserve_seams=s.bake_uv_preserve_seams,
            auto_unwrap=s.bake_auto_unwrap,
            recalc_normals=s.bake_recalc_normals,
            cage_extrusion=s.bake_cage_extrusion,
            max_ray_distance=s.bake_max_ray_distance,
            passes=self.bake_passes,
        )
        if result["success"]:
            self.report({"INFO"}, f"Baked {', '.join(self.bake_passes)} → {target.name}")
        else:
            self.report({"ERROR"}, result.get("error", "Baking failed"))
            return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_BakeAllMaps(Remi_BakeOperatorMixin, Operator):
    """Bake all available maps onto the active mesh."""

    bl_idname = "remi.bake_all_maps"
    bl_label = "Bake All Maps"
    bl_description = "Bake albedo, roughness, normal, and AO maps onto the active target mesh"
    bl_options = {"REGISTER", "UNDO"}


class Remi_OT_BakeDiffuse(Remi_BakeOperatorMixin, Operator):
    """Bake only the albedo/diffuse map onto the active mesh."""

    bl_idname = "remi.bake_diffuse"
    bl_label = "Bake Albedo"
    bl_description = "Bake only the diffuse/albedo map onto the active target mesh"
    bake_passes = ("diffuse",)


class Remi_OT_BakeRoughness(Remi_BakeOperatorMixin, Operator):
    """Bake only the roughness map onto the active mesh."""

    bl_idname = "remi.bake_roughness"
    bl_label = "Bake Roughness"
    bl_description = "Bake only the roughness map onto the active target mesh"
    bake_passes = ("roughness",)


class Remi_OT_BakeNormal(Remi_BakeOperatorMixin, Operator):
    """Bake only the tangent-space normal map onto the active mesh."""

    bl_idname = "remi.bake_normal"
    bl_label = "Bake Normal"
    bl_description = "Bake only the tangent-space normal map onto the active target mesh"
    bake_passes = ("normal",)


class Remi_OT_BakeAO(Remi_BakeOperatorMixin, Operator):
    """Bake only ambient occlusion onto the active mesh."""

    bl_idname = "remi.bake_ao"
    bl_label = "Bake Ambient Occlusion"
    bl_description = "Bake only ambient occlusion onto the active target mesh"
    bake_passes = ("ao",)
