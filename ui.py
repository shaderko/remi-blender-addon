"""Focused single-object UI for the Remi workflow."""

import bpy
from bpy.types import Panel

from .application import get_application
from .features.base import session_command as _command
from .workflow.contracts import FeatureUIContext


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


def _draw_session(layout, context):
    state = context.window_manager.remi_session
    features = get_application().features

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
    stages = None
    for index, feature in enumerate(features):
        if index % 3 == 0:
            stages = layout.row(align=True)
        descriptor = feature.descriptor
        _stage(stages, state, descriptor.id, descriptor.name)
    layout.separator()

    controls = layout.column()
    controls.enabled = not state.busy
    features.get(state.stage).draw(
        controls,
        FeatureUIContext(blender_context=context, state=state),
    )

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
