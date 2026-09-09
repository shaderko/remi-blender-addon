"""Focused single-object UI for the Remi workflow."""

import bpy
from bpy.types import Panel

from . import instant_meshes


def _command(layout, command, text, icon="NONE"):
    operator = layout.operator("remi.session_command", text=text, icon=icon)
    operator.command = command
    return operator


def _stage(layout, state, stage, text):
    operator = layout.operator(
        "remi.session_stage",
        text=text,
        depress=state.stage == stage,
    )
    operator.stage = stage


def _draw_source(layout, context):
    obj = context.view_layer.objects.active

    layout.label(text="REMI", icon="MOD_REMESH")
    layout.label(text="One mesh from repair to bake")
    layout.separator()

    if not obj or obj.type != "MESH" or context.mode != "OBJECT":
        notice = layout.box()
        notice.label(text="Select one mesh in Object Mode", icon="INFO")
        return

    layout.label(text=obj.name, icon="OBJECT_DATA")
    row = layout.row(align=True)
    row.label(text=f"{len(obj.data.vertices):,} vertices")
    row.label(text=f"{len(obj.data.polygons):,} faces")
    layout.separator()
    start = layout.column()
    start.scale_y = 1.5
    start.operator("remi.start_session", text="Start Remi", icon="PLAY")
    layout.label(text="Locked until Finish or Cancel", icon="LOCKED")


def _draw_repair(layout, settings, state):
    layout.label(text="Repair", icon="MOD_REMESH")

    if state.interactive:
        notice = layout.box()
        notice.label(text="Manual repair active", icon="BRUSH_DATA")
        notice.label(text="Draw on the surface around one hole")
        notice.label(text="Release to apply · Esc or right-click to cancel")
        return

    manual = layout.box()
    manual.label(text="Manual · One Hole", icon="BRUSH_DATA")
    manual.label(text="Draw on the intact surface around the rim")
    row = manual.row(align=True)
    row.prop(settings, "targeted_ray_spacing", text="Ray px")
    row.prop(settings, "targeted_ray_depth_ratio", text="Depth")
    row = manual.row(align=True)
    row.prop(settings, "alpha_wrap_patch_resolution", text="Resolution")
    row.prop(settings, "alpha_wrap_patch_relax_iterations", text="Relax")
    action = manual.column()
    action.scale_y = 1.35
    action.operator("remi.draw_hole_patch", text="Draw Around Hole", icon="BRUSH_DATA")

    layout.separator()
    layout.label(text="Automatic Repair")
    layout.prop(settings, "hole_repair_method", text="Method")

    if settings.hole_repair_method == "ALPHA_WRAP":
        layout.prop(settings, "alpha_wrap_alpha_ratio", text="Hole Scale")
        layout.prop(settings, "alpha_wrap_auto_scale", text="Find Scale Automatically")
        if settings.alpha_wrap_auto_scale:
            row = layout.row(align=True)
            row.prop(settings, "alpha_wrap_max_ratio", text="Maximum")
            row.prop(settings, "alpha_wrap_coverage_target", text="Coverage")
        row = layout.row(align=True)
        row.prop(settings, "alpha_wrap_patch_ratio", text="Detection")
        row.prop(settings, "alpha_wrap_patch_rings", text="Overlap")
        layout.prop(settings, "alpha_wrap_offset_ratio", text="Surface Offset")
        layout.prop(settings, "alpha_wrap_executable", text="Helper")
        layout.prop(settings, "alpha_wrap_auto_build", text="Build Automatically")
        layout.operator("remi.build_alpha_wrap", text="Build Helper", icon="TOOL_SETTINGS")
    elif settings.hole_repair_method in {"HYBRID", "BOUNDARY"}:
        row = layout.row(align=True)
        row.prop(settings, "hole_max_sides", text="Max Loop")
        row.prop(settings, "hole_weld_distance", text="Weld")
    elif settings.hole_repair_method == "VOLUME":
        layout.prop(settings, "hole_close_ratio", text="Crack Size")
        layout.prop(settings, "volume_guide_voxel_scale", text="Resolution")
        layout.prop(settings, "volume_surface_fit_ratio", text="Surface Fit Reach")
        row = layout.row(align=True)
        row.prop(settings, "alpha_wrap_patch_ratio", text="Detection")
        row.prop(settings, "alpha_wrap_patch_rings", text="Overlap")

    if settings.hole_repair_method in {"HYBRID", "VOLUME"}:
        layout.prop(settings, "hole_detail_recovery", text="Recover Surface Detail")
        if settings.hole_detail_recovery and settings.hole_repair_method == "HYBRID":
            layout.prop(settings, "hole_detail_ratio", text="Detail Reach")

    layout.separator()
    action = layout.column()
    action.scale_y = 1.35
    _command(action, "REPAIR", "Run Repair", "MOD_REMESH")


def _draw_remesh(layout, settings):
    layout.label(text="Remesh", icon="MOD_NORMALEDIT")
    layout.prop(settings, "remesh_backend", text="Method")
    layout.prop(settings, "voxel_size", text="Voxel Size")
    if settings.remesh_backend == "VOLUME":
        layout.label(text="Closes gaps and fits back to the surface", icon="INFO")
        row = layout.row(align=True)
        row.prop(settings, "hole_close_ratio", text="Crack Size")
        row.prop(settings, "volume_guide_voxel_scale", text="Resolution")
        layout.prop(settings, "volume_surface_fit_ratio", text="Surface Fit Reach")
        layout.prop(settings, "volume_preserve_features", text="Preserve Sharp Creases")
        if settings.volume_preserve_features:
            row = layout.row(align=True)
            row.prop(settings, "volume_feature_angle", text="Feature")
            row.prop(settings, "volume_feature_reach", text="Reach")
    else:
        row = layout.row(align=True)
        row.prop(settings, "use_sdf_fillet", text="Fillet")
        row.prop(settings, "use_sdf_smoothing", text="Smooth")
        if settings.use_sdf_fillet:
            layout.prop(settings, "fillet_radius", text="Fillet Radius")
        if settings.use_sdf_smoothing:
            layout.prop(settings, "smoothing_iterations", text="Smooth Steps")

    layout.separator()
    action = layout.column()
    action.scale_y = 1.35
    _command(action, "REMESH", "Run Remesh", "MOD_NORMALEDIT")

    layout.separator()
    decimate = layout.box()
    decimate.label(text="Optional · Reduce Faces", icon="MOD_DECIM")
    row = decimate.row(align=True)
    row.prop(settings, "decimation_passes", text="Passes")
    row.prop(settings, "target_percentage", text="Keep")
    row = decimate.row(align=True)
    row.prop(settings, "decimation_preserve_detail", text="Preserve Detail")
    row.prop(settings, "decimation_with_texture", text="Keep Texture")
    _command(decimate, "DECIMATE", "Run MeshLab Decimation", "MOD_DECIM")


def _draw_uv(layout, settings):
    layout.label(text="UV", icon="UV")
    layout.prop(settings, "bake_uv_profile", text="Profile")
    row = layout.row(align=True)
    row.prop(settings, "bake_texture_size", text="Texture")
    row.prop(settings, "bake_uv_margin_px", text="Padding")
    layout.prop(settings, "bake_uv_preserve_seams", text="Preserve Marked Seams")
    layout.separator()
    action = layout.column()
    action.scale_y = 1.35
    _command(action, "UV", "Generate UV", "UV")


def _draw_bake(layout, settings):
    layout.label(text="Bake", icon="RENDER_STILL")
    layout.label(text="Uses original source checkpoint", icon="LOCKED")
    layout.prop(settings, "bake_texture_size", text="Texture Size")
    layout.prop(settings, "bake_auto_unwrap", text="Auto Unwrap")
    if settings.bake_auto_unwrap:
        row = layout.row(align=True)
        row.prop(settings, "bake_uv_method", text="UV Method")
        if settings.bake_uv_method == "REMI":
            row.prop(settings, "bake_uv_profile", text="Profile")
            layout.prop(settings, "bake_uv_margin_px", text="Padding")
        else:
            layout.prop(settings, "bake_uv_island_margin", text="Margin")
    row = layout.row(align=True)
    row.prop(settings, "bake_recalc_normals", text="Recalc Normals")
    row.prop(settings, "bake_half_scale", text="Half Scale")
    row = layout.row(align=True)
    row.prop(settings, "bake_cage_extrusion", text="Cage")
    row.prop(settings, "bake_max_ray_distance", text="Max Ray")
    layout.separator()
    action = layout.column(align=True)
    action.scale_y = 1.3
    _command(action, "BAKE_ALL", "Bake All Maps", "RENDER_STILL")
    row = action.row(align=True)
    _command(row, "BAKE_DIFFUSE", "Albedo")
    _command(row, "BAKE_ROUGHNESS", "Roughness")
    row = action.row(align=True)
    _command(row, "BAKE_NORMAL", "Normal")
    _command(row, "BAKE_AO", "AO")


def _draw_retopology(layout, context, state):
    layout.label(text="Retopology", icon="MOD_REMESH")
    instant_settings = context.scene.remi_instant_meshes
    if state.interactive:
        instant_meshes.draw_panel(layout, context, embedded=True)
        return

    row = layout.row(align=True)
    row.prop(instant_settings, "target_faces", text="Target")
    row.prop(instant_settings, "pure_quad", text="Pure Quads")
    row = layout.row(align=True)
    row.prop(instant_settings, "preserve_creases", text="Creases")
    if instant_settings.preserve_creases:
        row.prop(instant_settings, "crease_angle", text="Angle")
    layout.prop(instant_settings, "align_boundaries", text="Align Open Boundaries")
    layout.separator()
    action = layout.column()
    action.scale_y = 1.35
    _command(action, "INSTANT_START", "Start Interactive Retopology", "PLAY")

    layout.separator()
    automatic = layout.box()
    row = automatic.row()
    settings = context.scene.remi_settings
    row.prop(settings, "use_autoremesher", text="")
    row.label(text="Automatic · External")
    if settings.use_autoremesher:
        automatic.prop(settings, "autoremesher_executable", text="Executable")
        row = automatic.row(align=True)
        row.prop(settings, "ar_target_quads", text="Target")
        row.prop(settings, "ar_adaptivity", text="Adaptive")
        row = automatic.row(align=True)
        row.prop(settings, "ar_edge_scaling", text="Edge Scale")
        row.prop(settings, "ar_sharp_edge", text="Sharp")
        automatic.prop(settings, "ar_smooth_normal", text="Smooth Normals")
        _command(automatic, "AUTO_RETOPO", "Run AutoRemesher", "MOD_REMESH")


def _draw_session(layout, context):
    state = context.window_manager.remi_session
    settings = context.scene.remi_settings

    header = layout.row(align=True)
    header.label(text="REMI MODE", icon="LOCKED")
    header.label(text=state.object_name)

    stats = layout.row(align=True)
    stats.label(text=f"{state.current_vertices:,} verts · {state.current_faces:,} faces")
    if state.source_faces:
        ratio = state.current_faces / state.source_faces
        stats.label(text=f"{ratio:.0%}")

    status = layout.box()
    status.label(text=f"Current · {state.current_step}", icon="INFO")
    status.label(text=state.status or "Ready")
    if state.checkpoint_megabytes:
        status.label(text=f"Recovery on disk · {state.checkpoint_megabytes:.1f} MB")

    layout.separator()
    stages = layout.row(align=True)
    _stage(stages, state, "REPAIR", "Repair")
    _stage(stages, state, "REMESH", "Remesh")
    _stage(stages, state, "RETOPOLOGY", "Retopo")
    stages = layout.row(align=True)
    _stage(stages, state, "UV", "UV")
    _stage(stages, state, "BAKE", "Bake")
    layout.separator()

    controls = layout.column()
    controls.enabled = not state.busy
    if state.stage == "REPAIR":
        _draw_repair(controls, settings, state)
    elif state.stage == "REMESH":
        _draw_remesh(controls, settings)
    elif state.stage == "RETOPOLOGY":
        _draw_retopology(controls, context, state)
    elif state.stage == "UV":
        _draw_uv(controls, settings)
    else:
        _draw_bake(controls, settings)

    layout.separator()
    history = layout.row(align=True)
    history.enabled = not state.interactive
    back = history.row(align=True)
    back.enabled = state.can_undo and not state.busy
    _command(back, "UNDO", "Back", "TRIA_LEFT")
    redo = history.row(align=True)
    redo.enabled = state.can_redo and not state.busy
    _command(redo, "REDO", "Redo", "TRIA_RIGHT")
    reset = history.row(align=True)
    reset.enabled = state.step_index > 0 and not state.busy
    _command(reset, "RESET", "Start", "FILE_REFRESH")

    finish = layout.row(align=True)
    finish.enabled = not state.busy and not state.interactive
    finish.scale_y = 1.3
    _command(finish, "FINISH", "Finish", "CHECKMARK")
    _command(finish, "CANCEL", "Cancel Session", "X")


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
        state = getattr(context.window_manager, "remi_session", None)
        if state is not None and state.active:
            _draw_session(layout, context)
        else:
            _draw_source(layout, context)


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
