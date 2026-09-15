"""Preset selection, editing and automatic-run adapters at the app boundary."""

from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from .application import get_application
from ..workflow.automatic import AutomaticFlow
from ..workflow.presets import PresetStore, apply_settings, capture_settings, checked_settings, validate_actions, validate_document
from ..workflow.session_operators import RemiSessionController


DEFAULT_ACTIONS = ("REMESH", "DECIMATE", "UV", "BAKE_ALL")
_enum_items = []
_store_override = None  # Isolated file store injection for tests.


def preset_store():
    return _store_override or PresetStore(Path(bpy.utils.user_resource("CONFIG")) / "remi" / "flow-presets")


def preset_items(_owner, _context):
    # Blender keeps references to enum strings. Retain them until the list changes.
    global _enum_items
    entries = [
        ("DEFAULT", "Default · Remesh to Bake", "Voxel remesh, MeshLab decimation, UV unwrap, and all texture maps"),
        ("CURRENT", "Current Settings", "Run the enabled stages with the displayed scene settings"),
    ]
    try:
        entries += [(key, name, "Saved full-flow preset") for key, name in preset_store().entries()]
    except OSError:
        pass
    if entries != _enum_items:
        _enum_items = entries
    return _enum_items


class RemiFlowStep(PropertyGroup):
    action_id: StringProperty()
    enabled: BoolProperty(default=True)


def preset_changed(owner, context):
    if owner.preset == "CURRENT" and not owner.steps:
        initialize_steps(owner)


def get_preset(owner):
    identifier = owner.get("preset_identifier", "DEFAULT")
    return next((i for i, item in enumerate(preset_items(owner, None)) if item[0] == identifier), 0)


def set_preset(owner, index):
    items = preset_items(owner, None)
    if 0 <= index < len(items):
        owner["preset_identifier"] = items[index][0]


class RemiFlowSettings(PropertyGroup):
    preset: EnumProperty(name="Preset", items=preset_items, get=get_preset, set=set_preset, update=preset_changed)
    steps: CollectionProperty(type=RemiFlowStep)
    edit_action: StringProperty(default="REMESH")
    show_settings: BoolProperty(name="Flow Settings", default=False)
    last_result: StringProperty(default="", options={"SKIP_SAVE"})


def automatic_actions():
    features = get_application().features
    return [features.require_action(action.id) for feature in features for action in feature.descriptor.actions if action.automatic]


def initialize_steps(flow, enabled=DEFAULT_ACTIONS):
    flow.steps.clear()
    for action in automatic_actions():
        step = flow.steps.add()
        step.action_id = action.id
        step.enabled = action.id in enabled


def current_document(context, name="Current Settings"):
    flow = context.scene.remi_flow
    actions = [step.action_id for step in flow.steps if step.enabled] if flow.steps else list(DEFAULT_ACTIONS)
    return {"version": 1, "name": name, "actions": actions, "settings": capture_settings(context.scene.remi_settings)}


def selected_document(context):
    identifier = context.scene.remi_flow.get("preset_identifier", "DEFAULT")
    if identifier == "CURRENT":
        document = current_document(context)
    elif identifier == "DEFAULT":
        values = capture_settings(context.scene.remi_settings, defaults=True)
        values.update(remesh_backend="VOXEL", use_hole_repair=False,
                      decimation_with_texture=False, bake_uv_method="REMI")
        document = {"version": 1, "name": "Default · Remesh to Bake", "actions": list(DEFAULT_ACTIONS), "settings": values}
    else:
        document = preset_store().load(identifier)
    validate_plan(context, document)
    return document


def validate_plan(context, document):
    validate_document(document)
    features = get_application().features
    validate_actions(features, document["actions"])
    checked_settings(context.scene.remi_settings, document["settings"])
    actions = document["actions"]
    if "BAKE_ALL" in actions and any(action.startswith("BAKE_") and action != "BAKE_ALL" for action in actions):
        raise ValueError("Choose Bake All Maps or individual bake passes, not both")
    feature_order = {feature.descriptor.id: i for i, feature in enumerate(features)}
    order = [feature_order[features.require_action(action).feature_id] for action in actions]
    if order != sorted(order):
        raise ValueError("Flow stages must follow the Repair → Remesh → Retopology → UV → Bake order")
    if "DECIMATE" in actions and "REMESH" in actions and document["settings"].get("decimation_with_texture"):
        raise ValueError("Disable Keep Texture for decimation after remeshing; textures are transferred from the original at Bake")


class Remi_OT_EditFlowPreset(Operator):
    bl_idname = "remi.edit_flow_preset"
    bl_label = "Edit Preset"
    bl_description = "Load the selected preset into editable current settings"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        state = context.window_manager.remi_session
        return not state.busy and not state.interactive and not state.automatic

    def execute(self, context):
        try:
            document = selected_document(context)
            apply_settings(context.scene.remi_settings, document["settings"])
            flow = context.scene.remi_flow
            initialize_steps(flow, document["actions"])
            flow.preset = "CURRENT"
            flow.show_settings = True
            flow.edit_action = document["actions"][0]
        except (ValueError, OSError, KeyError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_SaveFlowPreset(Operator):
    bl_idname = "remi.save_flow_preset"
    bl_label = "Save Current Settings as Preset"
    bl_description = "Save enabled flow stages and all current stage settings for reuse on other models"

    name: StringProperty(name="Preset Name", default="My Flow", maxlen=80)
    replace_existing: BoolProperty(name="Replace a preset with the same name", default=False)

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_session", None)
        return state is not None and not state.busy and not state.interactive and not state.automatic

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            document = current_document(context, self.name.strip())
            validate_plan(context, document)
            store = preset_store()
            same = [key for key, name in store.entries() if name.casefold() == self.name.strip().casefold()]
            if same and not self.replace_existing:
                raise ValueError("That preset name exists. Enable Replace or choose another name.")
            identifier = store.save(document, same[0] if same else None)
            preset_items(None, context)
            context.scene.remi_flow.preset = identifier
            context.scene.remi_flow.show_settings = False
            self.report({"INFO"}, f"Saved preset: {document['name']}")
        except (ValueError, OSError, KeyError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_DeleteFlowPreset(Operator):
    bl_idname = "remi.delete_flow_preset"
    bl_label = "Delete Preset"
    bl_description = "Delete the selected saved flow preset"

    @classmethod
    def poll(cls, context):
        return not context.window_manager.remi_session.automatic and context.scene.remi_flow.preset not in {"DEFAULT", "CURRENT"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            preset_store().delete(context.scene.remi_flow.preset)
            context.scene.remi_flow.preset = "DEFAULT"
        except (ValueError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_EditFlowStage(Operator):
    bl_idname = "remi.edit_flow_stage"
    bl_label = "Stage Settings"
    action_id: StringProperty()

    def execute(self, context):
        if not context.scene.remi_flow.steps:
            initialize_steps(context.scene.remi_flow)
        action = get_application().features.action(self.action_id)
        if action is None or not action.action.automatic:
            return {"CANCELLED"}
        context.scene.remi_flow.edit_action = self.action_id
        context.scene.remi_flow.show_settings = True
        return {"FINISHED"}


class Remi_OT_StopFullFlow(Operator):
    bl_idname = "remi.stop_full_flow"
    bl_label = "Stop After Current Stage"
    bl_description = "Stop before the next stage and keep the result and source recovery available"

    @classmethod
    def poll(cls, context):
        return context.window_manager.remi_session.automatic

    def execute(self, context):
        state = context.window_manager.remi_session
        state.flow_stop_requested = True
        state.status = "Stopping after the current stage…"
        return {"FINISHED"}


class Remi_OT_RunFullFlow(RemiSessionController, Operator):
    bl_idname = "remi.run_full_flow"
    bl_label = "Run Full Flow"
    bl_description = "Run every preset stage automatically, bake from the original, and keep the completed result"
    bl_options = {"REGISTER", "UNDO"}
    _flow = None

    def _start(self, context):
        app = get_application()
        self._flow = AutomaticFlow(app.session, app.features)
        self._flow.start(context, selected_document(context))
        context.scene.remi_flow.last_result = ""

    def invoke(self, context, event):
        try:
            self._start(context)
        except Exception as exc:
            context.scene.remi_flow.last_result = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(0.12, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            self._start(context)
            while True:
                outcome = self._flow.tick(context)
                if outcome != "RUNNING":
                    break
            if outcome == "FINISHED":
                self.report({"INFO"}, context.scene.remi_flow.last_result)
                return {"FINISHED"}
            self.report({"WARNING"}, context.window_manager.remi_session.status)
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def modal(self, context, event):
        state = context.window_manager.remi_session
        if not state.active or not state.automatic:
            # On failure/stop, this same modal operator becomes the manual
            # session controller so Back, Finish and Cancel still work.
            return RemiSessionController.modal(self, context, event)
        if event.type == "ESC" and event.value == "PRESS":
            state.flow_stop_requested = True
            return {"RUNNING_MODAL"}
        if event.type == "Z" and event.value == "PRESS" and event.ctrl:
            return {"RUNNING_MODAL"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            if not get_application().session.ensure_active_object(context):
                raise RuntimeError("The full-flow working object is missing")
            outcome = self._flow.tick(context)
            if outcome == "FINISHED":
                self._stop_timer(context)
                self.report({"INFO"}, context.scene.remi_flow.last_result)
                return {"FINISHED"}
            if outcome in {"FAILED", "STOPPED"}:
                self.report({"WARNING"}, state.status)
        except Exception as exc:
            state.automatic = False
            state.busy = False
            state.status = f"Full flow stopped: {exc}"
            self.report({"ERROR"}, state.status)
        finally:
            for area in context.screen.areas if context.screen else ():
                area.tag_redraw()
        return {"RUNNING_MODAL"}


CLASSES = (RemiFlowStep, RemiFlowSettings, Remi_OT_EditFlowPreset, Remi_OT_SaveFlowPreset,
           Remi_OT_DeleteFlowPreset, Remi_OT_EditFlowStage, Remi_OT_StopFullFlow, Remi_OT_RunFullFlow)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.remi_flow = PointerProperty(type=RemiFlowSettings)


def unregister():
    if hasattr(bpy.types.Scene, "remi_flow"):
        del bpy.types.Scene.remi_flow
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
