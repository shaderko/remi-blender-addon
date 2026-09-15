"""Blender-backed observable state for one active Remi session."""

from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup


class RemiSessionState(PropertyGroup):
    active: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    busy: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    interactive: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    automatic: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    flow_stop_requested: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    flow_total: IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})
    flow_completed: IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})
    session_id: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    object_name: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    status: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    current_step: StringProperty(default="Source", options={"HIDDEN", "SKIP_SAVE"})
    step_index: IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})
    can_undo: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    can_redo: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    source_faces: IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})
    current_faces: IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})
    current_vertices: IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})
    checkpoint_megabytes: FloatProperty(default=0.0, options={"HIDDEN", "SKIP_SAVE"})
    stage: StringProperty(default="REPAIR", options={"HIDDEN", "SKIP_SAVE"})
