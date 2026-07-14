def draw_panel(layout, context):
    settings = context.scene.remi_instant_meshes
    box = layout.box()
    header = box.row()
    header.label(text="Instant Meshes (Interactive)", icon="MOD_REMESH")

    if not settings.session_active:
        box.label(text="Native Apple Silicon field solver", icon="INFO")
        row = box.row(align=True)
        row.prop(settings, "target_faces", text="Target")
        row.prop(settings, "pure_quad", text="Pure Quads")
        row = box.row(align=True)
        row.prop(settings, "preserve_creases", text="Creases")
        if settings.preserve_creases:
            row.prop(settings, "crease_angle", text="Angle")
        box.prop(settings, "align_boundaries", text="Align Open Boundaries")
        advanced = box.column(align=True)
        row = advanced.row(align=True)
        row.prop(settings, "extrinsic", text="Extrinsic")
        row.prop(settings, "deterministic", text="Deterministic")
        advanced.prop(settings, "smooth_iterations", text="Projection Steps")
        box.separator(factor=0.4)
        button = box.column()
        button.scale_y = 1.25
        button.operator("remi.instant_meshes_start", icon="PLAY")
        return

    status = box.box()
    status.label(text=settings.status, icon="INFO")
    if settings.progress < 1.0:
        progress = status.row()
        progress.enabled = False
        progress.prop(settings, "progress", text=f"Progress {settings.progress:.0%}", slider=True)

    tools = box.box()
    tools.label(text="Surface Guides", icon="GREASEPENCIL")
    tools.label(text="Draw on the mesh; release to rebuild the preview.")
    row = tools.row(align=True)
    orientation = row.operator("remi.instant_meshes_draw", text="Orientation Comb", icon="BRUSH_DATA")
    orientation.stroke_type = "ORIENTATION"
    edge = row.operator("remi.instant_meshes_draw", text="Output Edge", icon="MOD_EDGESPLIT")
    edge.stroke_type = "EDGE"
    row = tools.row(align=True)
    row.operator("remi.instant_meshes_undo_stroke", text="Undo Guide")
    row.operator("remi.instant_meshes_clear_strokes", text="Clear")

    focus = box.box()
    focus.label(text="Viewport Focus", icon="SHADING_RENDERED")
    focus.prop(settings, "source_dimming", text="Dim Original", slider=True)
    row = focus.row(align=True)
    row.prop(settings, "preview_offset", text="Retopo Offset", slider=True)
    row.prop(settings, "preview_fill_opacity", text="Face Fill", slider=True)
    focus.prop(settings, "preview_xray", text="X-Ray Retopo (show back side)")

    fields = box.box()
    fields.label(text="Fields and Preview", icon="OVERLAY")
    row = fields.row(align=True)
    row.prop(settings, "show_orientation", text="Orientation")
    row.prop(settings, "show_position", text="Position")
    row = fields.row(align=True)
    row.prop(settings, "show_singularities", text="Singularities")
    row.prop(settings, "show_preview", text="Quad Preview")
    fields.prop(settings, "auto_update_preview", text="Auto-update After Guides")
    fields.prop(settings, "field_samples", text="Display Samples")
    row = fields.row(align=True)
    row.operator("remi.instant_meshes_solve_orientation", text="Rebuild Both Fields")
    row.operator("remi.instant_meshes_solve_position", text="Re-solve Position")
    fields.operator("remi.instant_meshes_preview", text="Update Quad Preview", icon="SHADING_WIRE")

    finish = box.column(align=True)
    finish.scale_y = 1.2
    finish.prop(settings, "hide_source", text="Hide Source on Accept")
    finish.operator("remi.instant_meshes_accept", text="Accept Retopology", icon="CHECKMARK")
    finish.operator("remi.instant_meshes_cancel", text="Cancel Session", icon="X")
