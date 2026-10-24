"""Texture workspace: source-faithful splat projection controls."""
from __future__ import annotations

from ...blender.splat_projection import existing_normal_map, projection_available, projection_preview
from .service import (
    active_mesh,
    appearance_source,
    can_project,
    external_python,
    source_is_valid,
)


def _draw_status(layout, state):
    if not state.status:
        return
    row = layout.row(align=True)
    if state.busy:
        row.label(text=state.status, icon="TIME")
        row.operator("remi.cancel_projection", text="", icon="CANCEL")
    elif state.status_kind == "SUCCESS":
        row.label(text="Projection ready", icon="CHECKMARK")
    elif state.status_kind == "ERROR":
        row.label(text=state.status, icon="ERROR")
    elif state.status_kind == "CANCELLED":
        row.label(text="Projection cancelled", icon="INFO")
    if not state.busy and state.directory:
        row.operator("wm.path_open", text="", icon="FILE_FOLDER").filepath = state.directory


def _resolve_source(mesh, settings):
    try:
        return appearance_source(mesh, settings)
    except Exception as exc:
        return None, None, str(exc)


def _draw_source(layout, mesh, settings):
    source_object, archive, label = _resolve_source(mesh, settings)
    if source_is_valid(source_object, archive):
        row = layout.row(align=True)
        row.label(text=f"Appearance · {label}", icon="IMAGE_DATA")
    else:
        warning = layout.box()
        warning.label(
            text=label or "No splat source linked to this mesh",
            icon="ERROR" if label else "INFO",
        )
        if not label:
            warning.label(text="Choose a Splat Source below", icon="INFO")

    layout.prop(settings, "source_object", text="Splat Source")
    if not mesh.data.uv_layers:
        layout.label(text="No UVs yet — projection will create them", icon="UV")


def _draw_settings(layout, settings, mesh):
    row = layout.row(align=True)
    row.prop(settings, "texture_size", text="Texture")
    row.prop(settings, "samples", text="Samples")
    layout.prop(settings, "normal_map_mode", text="Normal Map")
    if settings.normal_map_mode == "PROJECTED":
        layout.prop(settings, "normal_source", text="Normals")
        layout.prop(settings, "normal_strength", text="Normal Strength")
        layout.prop(settings, "normal_smoothing", text="Normal Smoothing")
    else:
        existing, _uv = existing_normal_map(mesh)
        if existing is not None and existing.name == f"{mesh.name} · projected normal":
            existing = None
        if existing is None:
            layout.label(text="No existing normal map; projected normals will be used", icon="INFO")
            layout.label(text="Bake → Normal in Optimize to keep a shape normal", icon="INFO")
    layout.prop(settings, "smooth_mesh", text="Smooth Mesh")
    row = layout.row(align=True)
    row.prop(settings, "fill_holes", text="Fill Holes")
    row.prop(settings, "fill_background", text="Fill Background")
    if not settings.fill_background:
        layout.prop(settings, "margin_px", text="Margin")
    layout.prop(settings, "pack_images", text="Pack Images")


def _draw_projection_state(layout, mesh, state):
    if not projection_available(mesh):
        return
    layout.separator()
    box = layout.box()
    box.label(text="Projected Texture", icon="TEXTURE")
    coverage = float(mesh.get("remi_texture_coverage", 0.0))
    filled = int(mesh.get("remi_texture_filled", 0))
    texels = int(mesh.get("remi_texture_texels", 0))
    size = int(mesh.get("remi_texture_size", 0))
    flatness = float(mesh.get("remi_texture_flatness", 0.0))
    row = box.row(align=True)
    if size:
        row.label(text=f"{size}px")
    row.label(text=f"Coverage {coverage:.0%}")
    if texels:
        row.label(text=f"{texels:,} texels")
    if flatness:
        box.label(text=f"Splat flatness {flatness:.0%}", icon="INFO")
        if flatness < 0.15:
            box.label(text="Volumetric splats: Auto uses clean mesh normals", icon="INFO")
            box.label(text="Use Keep Baked or force Gradient for more detail", icon="INFO")
    if filled:
        box.label(text=f"Filled {filled:,} low-evidence texels", icon="INFO")
    mode = projection_preview(mesh)
    row = box.row(align=True)
    row.operator("remi.texture_preview", text="Texture", depress=mode != "CONFIDENCE").mode = "TEXTURE"
    row.operator("remi.texture_preview", text="Confidence", depress=mode == "CONFIDENCE").mode = "CONFIDENCE"
    if state.directory:
        box.operator("wm.path_open", text="Open Projection Folder", icon="FILE_FOLDER").filepath = state.directory


def draw_texture(layout, context):
    settings = context.scene.remi_texture
    state = context.window_manager.remi_texture
    mesh = active_mesh(context)

    heading = layout.row(align=True)
    heading.label(text="Splat Projection", icon="TEXTURE")

    if mesh is None:
        notice = layout.box()
        notice.label(text="Select one mesh in Object Mode", icon="INFO")
        notice.label(text="The projector maps Gaussian appearance onto its UVs", icon="INFO")
        return

    layout.label(text=f"{mesh.name} · {len(mesh.data.vertices):,} vertices", icon="OBJECT_DATA")
    _draw_source(layout, mesh, settings)
    _draw_settings(layout, settings, mesh)

    layout.separator()
    source_object, archive, _label = _resolve_source(mesh, settings)
    source_ready = source_is_valid(source_object, archive)
    python_ready = bool(external_python(context))
    action = layout.column()
    action.scale_y = 1.5
    operator = action.operator(
        "remi.project_splats",
        text="Re-project Splats" if projection_available(mesh) else "Project Splats",
        icon="PLAY",
    )
    operator.enabled = bool(can_project(context) and source_ready and python_ready)
    if not python_ready:
        layout.label(text="Set the external Python in Generate settings", icon="INFO")
    if not source_ready:
        layout.label(text="Pick a Splat Source to continue", icon="INFO")
    if state.busy:
        layout.label(text="Blender stays interactive while the projector runs", icon="INFO")

    _draw_status(layout, state)
    _draw_projection_state(layout, mesh, state)
