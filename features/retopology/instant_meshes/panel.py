import textwrap

from .runtime import runtime


def _note(layout, text):
    for index, line in enumerate(textwrap.wrap(text, width=48)):
        layout.label(text=line, icon="INFO" if index == 0 else "NONE")


def draw_automatic_guides(layout, settings, *, active=False):
    box = layout.box()
    box.prop(settings, "auto_guides")
    if settings.auto_guides:
        row = box.row(align=True)
        row.prop(settings, "auto_circles")
        row.prop(settings, "auto_cylinders")
        box.prop(settings, "auto_curvature")
        box.prop(settings, "auto_quality_guard")
        box.prop(settings, "auto_guide_strength", slider=True)
        row = box.row(align=True)
        row.prop(settings, "auto_guide_tolerance", text="Tolerance")
        row.prop(settings, "auto_guide_limit", text="Limit")
    if not active:
        return
    controls = box.column()
    controls.enabled = not runtime.session.active
    controls.operator("remi.instant_meshes_detect_guides", text="Apply / Regenerate Guides", icon="FILE_REFRESH")
    if runtime.automatic_guides:
        controls.prop(settings, "show_auto_guides")
        enabled = sum(guide.enabled for guide in runtime.automatic_guides)
        controls.label(text=f"{enabled} / {len(runtime.automatic_guides)} suggestions enabled")
        page = min(settings.guide_page, (len(runtime.automatic_guides) - 1) // 8)
        if len(runtime.automatic_guides) > 8:
            controls.prop(settings, "guide_page", text="Page (from 0)")
        for index in range(page * 8, min((page + 1) * 8, len(runtime.automatic_guides))):
            guide = runtime.automatic_guides[index]
            toggle = controls.operator("remi.instant_meshes_toggle_guide",
                                       text=f"{guide.label} · {guide.confidence:.0%}",
                                       icon="CHECKBOX_HLT" if guide.enabled else "CHECKBOX_DEHLT")
            toggle.index = index
        controls.operator("remi.instant_meshes_clear_auto_guides", text="Remove Suggestions", icon="X")
    reset = controls.operator("remi.instant_meshes_detect_guides", text="Reset Disabled Suggestions")
    reset.reset = True
    for note in runtime.guide_notes[:4]:
        _note(box, note)
    if runtime.guidance_decision:
        _note(box, runtime.guidance_decision)
    _note(box, "Manual guides take priority. Suggestions do not guarantee loops.")


def draw_panel(layout, context, embedded=False):
    settings = context.scene.remi_instant_meshes
    box = layout if embedded else layout.box()
    if not embedded:
        header = box.row()
        header.label(text="Instant Meshes (Interactive)", icon="MOD_REMESH")

    if not settings.session_active:
        box.label(text="Native field solver", icon="INFO")
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
        draw_automatic_guides(box, settings)
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
    tools.enabled = not runtime.session.active
    row = tools.row(align=True)
    orientation = row.operator("remi.instant_meshes_draw", text="Orientation Comb", icon="BRUSH_DATA")
    orientation.stroke_type = "ORIENTATION"
    edge = row.operator("remi.instant_meshes_draw", text="Output Edge", icon="MOD_EDGESPLIT")
    edge.stroke_type = "EDGE"
    loop = tools.operator("remi.instant_meshes_draw", text="Draw Closed Edge Loop", icon="CURVE_BEZCIRCLE")
    loop.stroke_type = "EDGE"
    loop.closed = True
    row = tools.row(align=True)
    row.operator("remi.instant_meshes_undo_stroke", text="Undo Guide")
    row.operator("remi.instant_meshes_clear_strokes", text="Clear")

    draw_automatic_guides(box, settings, active=True)

    focus = box.box()
    focus.label(text="Viewport Focus", icon="SHADING_RENDERED")
    focus.prop(settings, "source_dimming", text="Dim Original", slider=True)
    row = focus.row(align=True)
    row.prop(settings, "preview_offset", text="Retopo Offset", slider=True)
    row.prop(settings, "preview_fill_opacity", text="Face Fill", slider=True)
    focus.prop(settings, "preview_xray", text="X-Ray Retopo (show back side)")

    fields = box.box()
    fields.label(text="Fields and Preview", icon="OVERLAY")
    fields.prop(settings, "target_faces", text="Target Faces")
    if runtime.pending_target_faces is not None:
        fields.label(
            text=f"Re-solving for {runtime.pending_target_faces:,} faces…",
            icon="TIME",
        )
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
    fields.prop(settings, "show_quality")
    if settings.show_quality and runtime.preview_quality:
        quality = runtime.preview_quality
        fields.label(text=f"Quads {quality['quad_fraction']:.0%} · irregular vertices {quality['irregular_vertices']:,}")
        fields.label(text=f"95% aspect ≤ {quality['aspect_p95']:.2f} · angle error ≤ {quality['angle_error_p95_deg']:.1f}°")
        if runtime.preview_guide_quality:
            loops = [g for g in runtime.preview_guide_quality if g["closed_loop"] is not None]
            if loops:
                fields.label(text=f"Measured circular loops: {sum(g['closed_loop'] for g in loops)} / {len(loops)}")

    if runtime.preview_warnings:
        warning = box.box()
        warning.label(text="Preview warning", icon="ERROR")
        for message in runtime.preview_warnings:
            warning.label(text=message)
        warning.label(text="You can still accept this result.", icon="CHECKMARK")

    finish = box.column(align=True)
    finish.scale_y = 1.2
    if embedded:
        finish.operator("remi.instant_meshes_session_accept", text="Accept Retopology", icon="CHECKMARK")
        finish.operator("remi.instant_meshes_session_cancel", text="Cancel Retopology", icon="X")
    else:
        finish.prop(settings, "hide_source", text="Hide Source on Accept")
        finish.operator("remi.instant_meshes_accept", text="Accept Retopology", icon="CHECKMARK")
        finish.operator("remi.instant_meshes_cancel", text="Cancel Session", icon="X")
