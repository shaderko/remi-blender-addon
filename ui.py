"""
UI Panel for Remi addon.
"""

import bpy
from bpy.types import Panel

from . import instant_meshes


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
        row.label(text="Remesh")
        if s.use_sdf_remesh:
            box.prop(s, "remesh_backend", text="Method")
            if s.remesh_backend == "VOLUME":
                warning = box.box()
                warning.label(text="Slow and memory intensive", icon="ERROR")
                warning.label(text="Closes holes and fits sharp creases")
                box.prop(s, "hole_close_ratio", text="Crack Size")
                box.prop(s, "volume_guide_voxel_scale", text="Volume Resolution")
                box.prop(s, "volume_surface_fit_ratio", text="Surface Fit Reach")
                box.prop(s, "volume_preserve_features", text="Preserve Sharp Creases")
                if s.volume_preserve_features:
                    row = box.row(align=True)
                    row.prop(s, "volume_feature_angle", text="Feature °")
                    row.prop(s, "volume_feature_reach", text="Reach")
            else:
                targeted = box.box()
                targeted.label(text="Targeted Hole Patching", icon="BRUSH_DATA")
                targeted.label(text="Draw on the surface around one hole")
                row = targeted.row(align=True)
                row.prop(s, "targeted_ray_spacing", text="Ray px")
                row.prop(s, "targeted_ray_depth_ratio", text="Depth")
                row = targeted.row(align=True)
                row.prop(s, "alpha_wrap_patch_resolution", text="Patch Resolution")
                row.prop(s, "alpha_wrap_patch_relax_iterations", text="Patch Relax")
                targeted.operator("remi.draw_hole_patch", text="Draw Around Hole", icon="BRUSH_DATA")
                box.prop(s, "use_hole_repair", text="Pre-Repair Holes")
                if s.use_hole_repair:
                    repair = box.box()
                    repair.prop(s, "hole_repair_method", text="Method")
                    if s.hole_repair_method == "ALPHA_WRAP":
                        repair.label(text="Preserves source triangles", icon="MOD_SHRINKWRAP")
                        repair.prop(s, "alpha_wrap_alpha_ratio", text="Start Hole Scale")
                        repair.prop(s, "alpha_wrap_auto_scale", text="Auto Find Hole Scale")
                        if s.alpha_wrap_auto_scale:
                            repair.prop(s, "alpha_wrap_max_ratio", text="Maximum Scale")
                            repair.prop(s, "alpha_wrap_coverage_target", text="Boundary Coverage")
                        repair.prop(s, "alpha_wrap_offset_ratio", text="Surface Offset")
                        repair.prop(s, "alpha_wrap_patch_ratio", text="Hole Detection")
                        repair.prop(s, "alpha_wrap_patch_rings", text="Border Overlap")
                        repair.prop(s, "alpha_wrap_patch_resolution", text="Patch Resolution")
                        repair.prop(s, "alpha_wrap_patch_relax_iterations", text="Patch Relax")
                        repair.prop(s, "alpha_wrap_executable", text="Helper")
                        repair.prop(s, "alpha_wrap_auto_build", text="Auto Build")
                        repair.operator("remi.build_alpha_wrap", text="Build Helper", icon="TOOL_SETTINGS")
                    if s.hole_repair_method in {"HYBRID", "BOUNDARY"}:
                        repair.prop(s, "hole_max_sides", text="Max Loop Edges")
                        repair.prop(s, "hole_weld_distance", text="Weld Distance")
                    if s.hole_repair_method in {"HYBRID", "VOLUME"}:
                        repair.prop(s, "hole_close_ratio", text="Crack Size")
                        repair.prop(s, "hole_detail_recovery", text="Recover Detail")
                        if s.hole_detail_recovery:
                            if s.hole_repair_method == "VOLUME":
                                repair.prop(s, "volume_surface_fit_ratio", text="Surface Fit Reach")
                            else:
                                repair.prop(s, "hole_detail_ratio", text="Detail Reach")
                    if s.hole_repair_method == "VOLUME":
                        repair.label(text="Fine volume used only as guide", icon="VOLUME_DATA")
                        repair.prop(s, "volume_guide_voxel_scale", text="Guide Resolution")
                        repair.prop(s, "alpha_wrap_patch_ratio", text="Hole Detection")
                        repair.prop(s, "alpha_wrap_patch_rings", text="Border Overlap")
                        repair.prop(s, "alpha_wrap_patch_resolution", text="Patch Resolution")
                        repair.prop(s, "alpha_wrap_patch_relax_iterations", text="Patch Relax")
                    button_text = (
                        "Prepare Hole Patches"
                        if s.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}
                        else "Repair Copy"
                    )
                    repair.operator("remi.repair_holes", text=button_text, icon="MOD_REMESH")
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
            action_text = "Run Closing Volume" if s.remesh_backend == "VOLUME" else "Remesh Copy"
            col.operator("remi.sdf_remesh", text=action_text)
            if s.remesh_backend == "VOXEL":
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
            box.prop(s, "decimation_preserve_detail", text="Preserve Detail")
            box.prop(s, "decimation_with_texture", text="Keep Texture (standalone only)")
            box.prop(s, "output_name_suffix", text="Suffix")
            box.separator(factor=0.3)
            box.operator("remi.decimate", text="Decimate")

        # ── Interactive Instant Meshes ─────────────────────────
        instant_meshes.draw_panel(layout, context)

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
            col.operator("remi.bake_all_maps", text="Bake All Maps", icon="RENDER_STILL")
            row = col.row(align=True)
            row.operator("remi.bake_diffuse", text="Albedo")
            row.operator("remi.bake_roughness", text="Roughness")
            row = col.row(align=True)
            row.operator("remi.bake_normal", text="Normal")
            row.operator("remi.bake_ao", text="AO")

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
        box = layout.box()
        box.label(text="Fused Parts", icon="AUTOMERGE_ON")
        box.label(text="Select the connected region first")
        col = box.column(align=True)
        col.operator("remi.smart_select_object", icon="RESTRICT_SELECT_OFF")
        col.operator("remi.detect_bridge", icon="MOD_EDGESPLIT")
        col.operator("remi.select_split_part", icon="RESTRICT_SELECT_OFF")
        col.operator("remi.split_by_bridge", icon="MOD_BOOLEAN")

        box = layout.box()
        box.label(text="Double Shell", icon="MOD_SOLIDIFY")
        box.label(text="Scans the entire visible mesh")
        col = box.column(align=True)
        col.operator("remi.select_inner_shell", icon="RESTRICT_SELECT_OFF")
        col.operator("remi.remove_inner_shell", icon="TRASH")


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
