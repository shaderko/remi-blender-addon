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
        layout.use_property_split = False
        layout.use_property_decorate = False
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

        # The enabled state is also the section's disclosure control: disabled
        # pipeline stages remain visible but collapse to one easy-to-scan row.
        # ── SDF Voxel Remesh ───────────────────────────────────
        box = layout.box()
        row = box.row()
        row.prop(s, "use_sdf_remesh", text="")
        row.label(text="SDF Voxel Remesh")
        if s.use_sdf_remesh:
            box.prop(s, "voxel_size", text="Voxel Size")
            row = box.row(align=True)
            row.prop(s, "use_sdf_fillet", text="Fillet")
            row.prop(s, "use_sdf_smoothing", text="Smooth")
            if s.use_sdf_fillet:
                box.prop(s, "fillet_radius", text="Fillet Radius")
            if s.use_sdf_smoothing:
                box.prop(s, "smoothing_iterations", text="Smooth Steps")
            box.separator(factor=0.3)
            col = box.column(align=True)
            col.scale_y = 1.15
            col.operator("remi.sdf_remesh", text="Remesh Copy")
            col.operator("remi.apply_remesh", text="Apply Modifier")

        # ── MeshLab Decimation ──────────────────────────────────
        box = layout.box()
        row = box.row()
        row.prop(s, "use_decimation", text="")
        row.label(text="MeshLab Decimation")
        if s.use_decimation:
            row = box.row(align=True)
            row.prop(s, "decimation_passes", text="Passes")
            row.prop(s, "target_percentage", text="Keep")
            box.prop(s, "output_name_suffix", text="Suffix")
            box.separator(factor=0.3)
            box.operator("remi.decimate", text="Decimate")

        # ── AutoRemesher ────────────────────────────────────────
        box = layout.box()
        row = box.row()
        row.prop(s, "use_autoremesher", text="")
        row.label(text="AutoRemesher (External)")
        if s.use_autoremesher:
            box.label(text="Runs as the final remesh step", icon="INFO")
            box.prop(s, "autoremesher_executable", text="Executable")
            box.separator(factor=0.3)
            row = box.row(align=True)
            row.prop(s, "ar_target_quads", text="Target")
            row.prop(s, "ar_adaptivity", text="Adaptive")
            row = box.row(align=True)
            row.prop(s, "ar_edge_scaling", text="Edge Scale")
            row.prop(s, "ar_sharp_edge", text="Sharp °")
            box.prop(s, "ar_smooth_normal", text="Smooth °")
            box.prop(s, "ar_hide_original", text="Hide Source")
            box.separator(factor=0.3)
            box.operator("remi.autoremesher", text="Run AutoRemesher")

        # ── Baking ──────────────────────────────────────────────
        box = layout.box()
        row = box.row()
        row.prop(s, "use_baking", text="")
        row.label(text="Bake Textures")
        if s.use_baking:
            box.label(text="Runs as the final pipeline step", icon="INFO")
            box.prop(s, "bake_texture_size", text="Texture Size")
            box.prop(s, "bake_auto_unwrap", text="Auto Unwrap")
            if s.bake_auto_unwrap:
                row = box.row(align=True)
                row.prop(s, "bake_uv_method", text="UV Method")
                row.prop(s, "bake_uv_island_margin", text="Margin")
            row = box.row(align=True)
            row.prop(s, "bake_recalc_normals", text="Recalc Normals")
            row.prop(s, "bake_half_scale", text="Half Scale")
            row = box.row(align=True)
            row.prop(s, "bake_cage_extrusion", text="Cage")
            row.prop(s, "bake_max_ray_distance", text="Max Ray")
            box.label(text="Source first, target last (active)", icon="INFO")
            col = box.column(align=True)
            col.operator("remi.bake_textures", text="Bake All Maps", icon="RENDER_STILL")
            row = col.row(align=True)
            row.operator("remi.bake_diffuse", text="Albedo")
            row.operator("remi.bake_roughness", text="Roughness")
            row.operator("remi.bake_normal", text="Normal")

        # ── Full Pipeline ───────────────────────────────────────
        box = layout.box()
        col = box.column(align=True)
        col.scale_y = 1.6
        col.operator("remi.full_pipeline", text="▶ Run Full Remi")


class Remi_PT_EditToolsPanel(Panel):
    """Edit Mode topology-selection tools."""

    bl_label = "Remi Selection Tools"
    bl_idname = "Remi_PT_EditToolsPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Remi"
    bl_context = "mesh_edit"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Select connected faces, then use:", icon="INFO")
        col = layout.column(align=True)
        col.operator("remi.smart_select_object", icon="RESTRICT_SELECT_OFF")
        col.operator("remi.detect_bridge", icon="MOD_EDGESPLIT")
        col.operator("remi.select_split_part", icon="RESTRICT_SELECT_OFF")
        col.operator("remi.split_by_bridge", icon="MOD_BOOLEAN")


# ============================================================
# Registration
# ============================================================

classes = [
    Remi_PT_MainPanel,
    Remi_PT_EditToolsPanel,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
