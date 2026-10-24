"""Focused single-object UI for the Remi workflow."""

import bpy
from bpy.props import PointerProperty
from bpy.types import Panel

from ..application import get_application
from ...features.edit_tools.panel import Remi_PT_EditToolsPanel
from ...features.generation.panel import draw_generation
from ...features.texture.panel import draw_texture
from ...workflow.contracts import FeatureUIContext
from .history_controls import draw_history_controls
from .session_header import draw_session_header
from .source_view import draw_source
from .workspace import RemiUIState, Remi_OT_SelectWorkspace, active_workspace, draw_workspace_tabs


def _stage(layout, state, stage, text):
    operator = layout.operator(
        "remi.session_stage",
        text=text,
        depress=state.stage == stage,
    )
    operator.stage = stage


def draw_session(layout, context):
    state = context.window_manager.remi_session
    features = get_application().features

    draw_session_header(layout, state)

    if state.automatic:
        layout.label(text=f"Full Flow · {state.flow_completed}/{state.flow_total} stages complete")
        layout.operator("remi.stop_full_flow", text="Stop After Current Stage", icon="PAUSE")
        layout.label(text="Each stage may keep Blender busy", icon="INFO")
        return

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
    draw_history_controls(layout, state)
    layout.operator("remi.save_flow_preset", text="Save Current Settings as Preset", icon="PRESET")


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
        draw_workspace_tabs(layout, context)
        layout.separator()
        state = getattr(context.window_manager, "remi_session", None)
        workspace = active_workspace(context)
        if workspace == "GENERATE":
            draw_generation(layout, context)
        elif workspace == "TEXTURE":
            draw_texture(layout, context)
        elif state is not None and state.active:
            draw_session(layout, context)
        else:
            draw_source(layout, context)


classes = [
    RemiUIState,
    Remi_OT_SelectWorkspace,
    Remi_PT_MainPanel,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.remi_ui = PointerProperty(type=RemiUIState)


def unregister():
    del bpy.types.WindowManager.remi_ui
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
