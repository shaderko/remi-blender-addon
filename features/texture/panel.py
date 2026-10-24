"""Texture workspace: source-faithful splat projection controls."""
from __future__ import annotations

from ...blender.gaussian_splats import native_splats_available
from ...blender.splat_projection import (
    MASK_GROUP,
    existing_normal_map,
    projection_available,
    projection_preview,
)
from ...blender.texture_layers import ordered_layer_indices
from .service import (
    active_mesh,
    appearance_source,
    can_project,
    external_python,
    source_is_valid,
)
from .view_camera import viewport_region


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
    row.prop(settings, "view_resolution", text="View")
    row = layout.row(align=True)
    row.prop(settings, "view_alpha_threshold", text="View Alpha")
    row.prop(settings, "pack_images", text="Pack Images")
    layout.prop(settings, "smooth_mesh", text="Smooth Mesh")

    bake = layout.box()
    bake.label(text="3D Bake (optional)", icon="MOD_REMESH")
    bake.prop(settings, "samples", text="Samples")
    bake.prop(settings, "normal_map_mode", text="Normal Map")
    if settings.normal_map_mode == "PROJECTED":
        bake.prop(settings, "normal_source", text="Normals")
        bake.prop(settings, "normal_strength", text="Normal Strength")
        bake.prop(settings, "normal_smoothing", text="Normal Smoothing")
    else:
        existing, _uv = existing_normal_map(mesh)
        if existing is not None and existing.name == f"{mesh.name} · projected normal":
            existing = None
        if existing is None:
            bake.label(text="No existing normal; projected normals will be used", icon="INFO")
            bake.label(text="Bake → Normal in Optimize to keep a shape normal", icon="INFO")
    row = bake.row(align=True)
    row.prop(settings, "fill_holes", text="Fill Holes")
    row.prop(settings, "fill_background", text="Fill Background")
    if not settings.fill_background:
        bake.prop(settings, "margin_px", text="Margin")


def _draw_mask(layout, mesh):
    box = layout.box()
    box.label(text="Projection Mask", icon="BRUSH_DATA")
    group = mesh.vertex_groups.get(MASK_GROUP)
    if group is None:
        box.label(text="No mask — full visible view", icon="INFO")
        box.operator("remi.paint_projection_mask", text="Paint Projection Mask", icon="BRUSH_DATA")
    else:
        box.label(text="Painted area only", icon="CHECKMARK")
        row = box.row(align=True)
        row.operator("remi.paint_projection_mask", text="Edit Mask", icon="BRUSH_DATA")
        row.operator("remi.clear_projection_mask", text="Clear", icon="X")
    box.label(text="Applies to Project from View layers", icon="INFO")


def _draw_mask_painting(layout, mesh):
    box = layout.box()
    box.label(text="Painting Projection Mask", icon="BRUSH_DATA")
    box.label(text="White projects; black skips", icon="INFO")
    box.label(text="Paint the area you want the next view layer to affect", icon="INFO")
    row = box.row(align=True)
    row.operator("remi.finish_projection_mask", text="Done", icon="CHECKMARK")
    if mesh.vertex_groups.get(MASK_GROUP) is not None:
        row.operator("remi.clear_projection_mask", text="Clear", icon="X")


def _draw_layers(layout, mesh):
    box = layout.box()
    box.label(text="Layers (bottom → top)", icon="RENDERLAYERS")
    entries = ordered_layer_indices(mesh)
    if not entries:
        box.label(text="No layers yet — project a view to start", icon="INFO")
        return
    for index, layer in entries:
        row = box.row(align=True)
        icon = "HIDE_OFF" if layer.visible else "HIDE_ON"
        row.prop(layer, "visible", text="", icon=icon, emboss=False)
        label = layer.name
        if layer.kind == "BASE":
            label += " · base"
        row.label(text=label)
        row.prop(layer, "opacity", text="", slider=True)
        remove = row.operator("remi.delete_texture_layer", text="", icon="X")
        remove.index = index


def _draw_projection_state(layout, mesh, state):
    if not projection_available(mesh):
        return
    layout.separator()
    box = layout.box()
    box.label(text="Projected Material", icon="TEXTURE")
    layers = list(mesh.remi_texture_layers)
    visible = sum(1 for layer in layers if layer.visible)
    row = box.row(align=True)
    row.label(text=f"{len(layers)} layer{'s' if len(layers) != 1 else ''} · {visible} visible")

    has_3d = bool(mesh.get("remi_texture_albedo"))
    if has_3d:
        coverage = float(mesh.get("remi_texture_coverage", 0.0))
        filled = int(mesh.get("remi_texture_filled", 0))
        texels = int(mesh.get("remi_texture_texels", 0))
        size = int(mesh.get("remi_texture_size", 0))
        flatness = float(mesh.get("remi_texture_flatness", 0.0))
        row = box.row(align=True)
        if size:
            row.label(text=f"{size}px")
        row.label(text=f"3D coverage {coverage:.0%}")
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
    if mesh.get("remi_texture_confidence_material"):
        row.operator("remi.texture_preview", text="Confidence", depress=mode == "CONFIDENCE").mode = "CONFIDENCE"
    if state.directory:
        box.operator("wm.path_open", text="Open Projection Folder", icon="FILE_FOLDER").filepath = state.directory


def draw_texture(layout, context):
    settings = context.scene.remi_texture
    state = context.window_manager.remi_texture
    active = context.view_layer.objects.active
    if context.mode in {"PAINT_WEIGHT", "WEIGHT_PAINT"} and active is not None and active.type == "MESH":
        heading = layout.row(align=True)
        heading.label(text="Splat Projection", icon="TEXTURE")
        layout.label(text=active.name, icon="OBJECT_DATA")
        _draw_mask_painting(layout, active)
        return

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
    _draw_mask(layout, mesh)

    source_object, archive, _label = _resolve_source(mesh, settings)
    source_ready = source_is_valid(source_object, archive)
    python_ready = bool(external_python(context))
    native_ready = native_splats_available()
    viewport_ready = viewport_region(context)[0] is not None

    layout.separator()
    view_action = layout.column()
    view_action.scale_y = 1.5
    view_operator = view_action.operator(
        "remi.project_view",
        text="Project from View",
        icon="CAMERA_DATA",
    )
    view_operator.enabled = bool(
        can_project(context) and source_ready and python_ready and native_ready and viewport_ready
    )
    bake_action = layout.row(align=True)
    bake_operator = bake_action.operator(
        "remi.project_splats",
        text="Project 3D",
        icon="MOD_REMESH",
    )
    bake_operator.enabled = bool(can_project(context) and source_ready and python_ready)
    if not native_ready:
        layout.label(text="View projection requires Blender 5.3+ native splats", icon="INFO")
        layout.label(text="Project 3D remains available on this version", icon="INFO")
    if not viewport_ready:
        layout.label(text="Open this panel in a 3D Viewport to project from view", icon="INFO")
    if not python_ready:
        layout.label(text="Set the external Python in Generate settings", icon="INFO")
    if not source_ready:
        layout.label(text="Pick a Splat Source to continue", icon="INFO")
    if state.busy:
        layout.label(text="Blender stays interactive while the projector runs", icon="INFO")

    _draw_status(layout, state)
    _draw_layers(layout, mesh)
    _draw_settings(layout, settings, mesh)
    _draw_projection_state(layout, mesh, state)
