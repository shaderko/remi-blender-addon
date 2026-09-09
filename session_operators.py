"""Blender operator adapters for the Remi session runtime."""

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from .application import get_application
from .session import runtime
from .workflow.contracts import ExecutionMode


CONTROL_COMMANDS = (
    ("UNDO", "Back", "Return to the previous committed Remi mesh"),
    ("REDO", "Redo", "Restore the reverted Remi mesh"),
    ("RESET", "Reset", "Return to the source mesh"),
    ("FINISH", "Finish", "Keep the current mesh and leave Remi"),
    ("CANCEL", "Cancel", "Restore the source mesh and leave Remi"),
)


def _command_items(_operator, _context):
    actions = [
        (registered.id, registered.action.name, registered.action.description)
        for feature in get_application().features
        for registered in (
            get_application().features.require_action(action.id)
            for action in feature.descriptor.actions
        )
    ]
    return actions + list(CONTROL_COMMANDS)


def _stage_items(_operator, _context):
    return [
        (feature.descriptor.id, feature.descriptor.name, feature.descriptor.name)
        for feature in get_application().features
    ]


class Remi_OT_StartSession(Operator):
    bl_idname = "remi.start_session"
    bl_label = "Start Remi"
    bl_description = "Lock the active mesh into a recoverable single-object Remi session"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_session", None)
        return bool(
            state is not None
            and not state.active
            and context.mode == "OBJECT"
            and context.active_object
            and context.active_object.type == "MESH"
        )

    def invoke(self, context, event):
        try:
            runtime.begin(context, context.active_object)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(0.12, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        # Tests and background scripts can create the session without a timer.
        try:
            runtime.begin(context, context.active_object)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}

    def _stop_timer(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _run_action(self, context, registered):
        try:
            if context.window is not None:
                context.window.cursor_modal_set("WAIT")
            runtime.execute_action(context, registered)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
        finally:
            if context.window is not None:
                try:
                    context.window.cursor_modal_restore()
                except Exception:
                    pass

    def _process_command(self, context, command):
        state = runtime.state(context)
        registered = get_application().features.action(command)
        if registered is not None:
            if registered.action.mode == ExecutionMode.INTERACTIVE:
                runtime.start_interactive_action(context, registered)
            else:
                self._run_action(context, registered)
        elif command == "UNDO":
            runtime.undo(context)
        elif command == "REDO":
            runtime.redo(context)
        elif command == "RESET":
            runtime.reset(context)
        elif command == "FINISH":
            runtime.finish(context)
            self._stop_timer(context)
            self.report({"INFO"}, "Remi session finished")
            return {"FINISHED"}
        elif command == "CANCEL":
            runtime.cancel(context)
            self._stop_timer(context)
            self.report({"INFO"}, "Remi session cancelled; source restored")
            return {"CANCELLED"}
        state.busy = False
        return None

    def modal(self, context, event):
        state = runtime.state(context)
        if not state.active:
            self._stop_timer(context)
            return {"FINISHED"}

        if event.type == "Z" and event.value == "PRESS" and event.ctrl:
            if state.interactive:
                state.status = "Finish or cancel the active interactive tool before going back"
                return {"RUNNING_MODAL"}
            command = "REDO" if event.shift else "UNDO"
            try:
                runtime.queue(context, command)
            except RuntimeError as exc:
                self.report({"INFO"}, str(exc))
            return {"RUNNING_MODAL"}

        if event.type == "ESC" and event.value == "PRESS":
            if state.interactive:
                if state.stage == "REPAIR":
                    state.status = "Press Esc or right-click in the viewport to cancel manual repair"
                else:
                    state.status = "Use Cancel Retopology to leave the interactive tool"
                return {"RUNNING_MODAL"}
            state.status = "Use Finish to keep the result or Cancel Session to restore the source."
            return {"RUNNING_MODAL"}

        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        if not runtime.ensure_active_object(context):
            state.status = "The locked Remi object is missing."
            return {"RUNNING_MODAL"}

        command = runtime.pop_command()
        if not command:
            return {"RUNNING_MODAL"}
        try:
            result = self._process_command(context, command)
            if result:
                return result
        except Exception as exc:
            state.busy = False
            state.status = str(exc)
            self.report({"ERROR"}, str(exc))
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        self._stop_timer(context)
        if runtime.state(context).active:
            runtime.cancel(context)


class Remi_OT_SessionCommand(Operator):
    bl_idname = "remi.session_command"
    bl_label = "Remi Session Command"
    bl_description = "Run an action on the locked Remi mesh"

    command: EnumProperty(
        items=_command_items,
    )

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_session", None)
        return bool(
            state is not None
            and state.active
            and not state.busy
            and not state.interactive
        )

    def invoke(self, context, event):
        if self.command in {"RESET", "CANCEL"}:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        try:
            runtime.queue(context, self.command)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_SessionStage(Operator):
    bl_idname = "remi.session_stage"
    bl_label = "Choose Remi Stage"
    bl_description = "Show controls for this Remi stage"

    stage: EnumProperty(
        items=_stage_items,
    )

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_session", None)
        return bool(
            state is not None
            and state.active
            and not state.busy
            and not state.interactive
        )

    def execute(self, context):
        context.window_manager.remi_session.stage = self.stage
        return {"FINISHED"}


CLASSES = (
    Remi_OT_StartSession,
    Remi_OT_SessionCommand,
    Remi_OT_SessionStage,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
