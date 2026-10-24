"""Top-level source generation and mesh optimization navigation."""
import bpy
from bpy.props import EnumProperty
from bpy.types import Operator, PropertyGroup


WORKSPACES = (
    ("GENERATE", "Generate", "Create a model from an image", "IMAGE_DATA", 0),
    ("OPTIMIZE", "Optimize", "Optimize an existing mesh", "MOD_DECIM", 1),
)


class RemiUIState(PropertyGroup):
    workspace: EnumProperty(items=WORKSPACES, default="GENERATE", options={"SKIP_SAVE"})


def workspace_locked(context):
    session = getattr(context.window_manager, "remi_session", None)
    generation = getattr(context.window_manager, "remi_generation", None)
    return bool(
        (session and session.active)
        or (generation and (generation.busy or generation.mesh_busy))
    )


def active_workspace(context):
    session = getattr(context.window_manager, "remi_session", None)
    if session and session.active:
        return "OPTIMIZE"
    generation = getattr(context.window_manager, "remi_generation", None)
    if generation and (generation.busy or generation.mesh_busy):
        return "GENERATE"
    state = getattr(context.window_manager, "remi_ui", None)
    return state.workspace if state is not None else "OPTIMIZE"


class Remi_OT_SelectWorkspace(Operator):
    bl_idname = "remi.select_workspace"
    bl_label = "Switch Remi Workspace"
    bl_description = "Switch between image generation and mesh optimization"

    workspace: EnumProperty(items=WORKSPACES)

    @classmethod
    def poll(cls, context):
        return not workspace_locked(context)

    def execute(self, context):
        context.window_manager.remi_ui.workspace = self.workspace
        if context.screen:
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        return {"FINISHED"}


def draw_workspace_tabs(layout, context):
    selected = active_workspace(context)
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.enabled = not workspace_locked(context)
    for identifier, label, _description, icon, _index in WORKSPACES:
        row.operator("remi.select_workspace", text=label, icon=icon,
                     depress=selected == identifier).workspace = identifier
