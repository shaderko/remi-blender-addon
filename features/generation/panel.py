"""Focused generation controls with runtime details kept in a popover."""
from pathlib import Path

import bpy

from ...blender.gaussian_splats import native_splats_available
from ...blender.reconstructed_mesh import is_reconstructed_mesh, mesh_for_splat
from .mesh_operators import can_convert, splat_source, runtime as mesh_runtime


def _draw_status(layout, state):
    if not state.status:
        return
    row = layout.row(align=True)
    if state.busy:
        row.label(text=state.status, icon="TIME")
        row.operator("remi.cancel_generation", text="", icon="CANCEL")
    elif state.status_kind == "SUCCESS":
        row.label(text="Model ready", icon="CHECKMARK")
    elif state.status_kind == "CANCELLED":
        row.label(text="Cancelled")
    elif state.status_kind == "ERROR":
        row.label(text=state.status, icon="ERROR")
    if not state.busy and state.directory:
        row.operator("wm.path_open", text="", icon="FILE_FOLDER").filepath = state.directory


def draw_generation(layout, context):
    settings = context.scene.remi_generation
    state = context.window_manager.remi_generation
    heading = layout.row(align=True)
    heading.label(text="Image")
    heading.popover(panel="Remi_PT_GenerationSettings", text="", icon="PREFERENCES")
    controls = layout.column()
    controls.enabled = not state.busy and context.mode == "OBJECT"
    controls.prop(settings, "image", text="")
    controls.separator()
    actions = controls.row(align=True)
    actions.scale_y = 1.6
    primary = actions.row(align=True)
    primary.enabled = bool(settings.image.strip())
    primary.operator("remi.generate_splats", text="Generate", icon="PLAY")
    actions.operator("remi.import_splats", text="", icon="IMPORT")
    if context.mode != "OBJECT":
        layout.label(text="Switch to Object Mode to generate", icon="INFO")
    _draw_status(layout, state)
    _draw_mesh_controls(layout, context, state, settings)


def _draw_mesh_controls(layout, context, state, settings):
    if state.mesh_busy:
        row = layout.row(align=True)
        row.label(text=state.mesh_status or "Creating mesh…", icon="TIME")
        row.operator("remi.cancel_mesh", text="", icon="CANCEL")
        return
    active = context.view_layer.objects.active
    source = splat_source(context)
    mesh = mesh_for_splat(source) if source else (active if is_reconstructed_mesh(active) else None)
    if mesh is not None:
        row = layout.row(align=True)
        row.label(text=f"Mesh ready · {len(mesh.data.polygons):,} triangles", icon="CHECKMARK")
        mesh_path = mesh.get("remi_mesh_ply")
        if mesh_path:
            row.operator("wm.path_open", text="", icon="FILE_FOLDER").filepath = str(Path(mesh_path).parent)
        row.operator("remi.select_workspace", text="Optimize", icon="MOD_DECIM").workspace = "OPTIMIZE"
    current = source.name if source is not None else (active.name if active is not None else "")
    if state.mesh_source_object == current:
        if state.mesh_status_kind == "ERROR" and state.mesh_status:
            layout.label(text=state.mesh_status, icon="ERROR")
        elif state.mesh_status_kind == "CANCELLED" and state.mesh_status:
            layout.label(text=state.mesh_status)
    if can_convert(context):
        row = layout.row()
        row.scale_y = 1.35
        row.operator("remi.create_mesh", text="Recreate Mesh" if mesh else "Create Mesh", icon="MOD_REMESH")


class Remi_PT_GenerationSettings(bpy.types.Panel):
    bl_label = "Generation Settings"
    bl_idname = "Remi_PT_GenerationSettings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 20

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        controls = layout.column()
        controls.enabled = not context.window_manager.remi_generation.busy
        settings = context.scene.remi_generation
        controls.prop(settings, "gaussians")
        controls.prop(settings, "seed")
        if not native_splats_available():
            controls.prop(settings, "preview_limit")
        controls.separator()
        controls.prop(settings, "python")
        controls.prop(settings, "models")
        controls.prop(settings, "output")
        controls.separator()
        controls.label(text="Reconstructed Mesh")
        controls.prop(settings, "mesh_detail")
        controls.prop(settings, "mesh_smoothing")
        controls.prop(settings, "mesh_output")
