"""
UI Panel for Remi addon.
"""

import bpy
from bpy.types import Panel


class Remi_PT_MainPanel(Panel):
    bl_label = "Remi"
    bl_idname = "Remi_PT_MainPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Remi"

    def draw(self, context):
        layout = self.layout
        s = context.scene.remi_settings
        obj = context.view_layer.objects.active

        # ── Active Mesh ─────────────────────────────────────────
        if obj and obj.type == "MESH":
            box = layout.box()
            row = box.row()
            row.label(text="Active", icon="OBJECT_DATA")
            row.label(text=obj.name)
            row = box.row()
            row.label(text=f"{len(obj.data.vertices):,} verts")
            row.label(text=f"{len(obj.data.polygons):,} faces")

        # ── SDF Voxel Remesh ───────────────────────────────────
        box = layout.box()
        box.label(text="SDF Voxel Remesh")
        box.prop(s, "detail", slider=True)

        row = box.row()
        row.prop(s, "use_sdf_fillet", text="Fillet")
        row.prop(s, "use_sdf_smoothing", text="Smooth")
        if s.use_sdf_fillet:
            box.prop(s, "fillet_radius")
        if s.use_sdf_smoothing:
            box.prop(s, "smoothing_iterations")

        box.separator(factor=0.3)
        col = box.column(align=True)
        col.scale_y = 1.2
        col.operator("remi.sdf_remesh", text="SDF Remesh (Copy)")
        col.operator("remi.apply_remesh", text="Apply Modifier")

        # ── AutoRemesher ────────────────────────────────────────
        box = layout.box()
        row = box.row()
        row.prop(s, "use_autoremesher", text="")
        row.label(text="AutoRemesher (External)")
        if s.use_autoremesher:
            row.label(text="  last step")

        sub = box.column(align=True)
        sub.prop(s, "autoremesher_executable")
        sub.separator(factor=0.3)
        sub.prop(s, "ar_target_quads")
        sub.prop(s, "ar_adaptivity")
        sub.prop(s, "ar_edge_scaling")
        sub.prop(s, "ar_sharp_edge")
        sub.prop(s, "ar_smooth_normal")
        sub.separator(factor=0.3)
        sub.prop(s, "ar_hide_original")
        sub.separator(factor=0.3)
        sub.operator("remi.autoremesher", text="Run AutoRemesher")

        # ── MeshLab Decimation ──────────────────────────────────
        box = layout.box()
        box.label(text="MeshLab Decimation")
        box.prop(s, "decimation_passes")
        box.prop(s, "target_percentage")
        box.separator(factor=0.3)
        box.prop(s, "output_name_suffix")
        box.separator(factor=0.3)
        box.operator("remi.decimate", text="Decimate via MeshLab")

        # ── Baking ──────────────────────────────────────────────
        box = layout.box()
        row = box.row()
        row.prop(s, "use_baking", text="")
        row.label(text="Bake Textures")
        row.label(text="(last step)" if s.use_baking else "")

        box.prop(s, "bake_texture_size")
        box.prop(s, "bake_uv_method")
        box.prop(s, "bake_uv_island_margin")
        box.operator("remi.bake_textures", text="Bake Textures")

        # ── Full Pipeline ───────────────────────────────────────
        box = layout.box()
        col = box.column(align=True)
        col.scale_y = 1.6
        col.operator("remi.full_pipeline", text="▶ Run Full Remi")


# ============================================================
# Registration
# ============================================================

classes = [
    Remi_PT_MainPanel,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
