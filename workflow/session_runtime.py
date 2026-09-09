"""Single-object Remi workflow with disk-backed recovery checkpoints."""

from __future__ import annotations

import time
import uuid

import bpy

from ..blender.session_objects import SessionObjectStore
from ..storage.disk import SESSION_ID_KEY, SessionDiskService
from .contracts import FeatureExecutionContext
from .registry import FeatureRegistry, RegisteredAction
from .state import RemiSessionState


class RemiSessionRuntime:
    """Own one visible object and a bounded set of on-disk checkpoints."""

    def __init__(self):
        self.objects = SessionObjectStore()
        self.features = None
        self.disk = None
        self.pending_command = ""
        self.last_step_label = ""
        self.previous_step_label = ""
        self.previous_stage = "REPAIR"
        self.redo_step_label = ""
        self.redo_stage = "REPAIR"
        self.pending_step_label = ""
        self.pending_stage = "REPAIR"
        self.interactive_copy = None

    def configure_features(self, features: FeatureRegistry | None):
        if self.state_is_active():
            raise RuntimeError("Cannot replace Remi features during an active session")
        self.features = features

    @staticmethod
    def state_is_active():
        state = getattr(bpy.context.window_manager, "remi_session", None)
        return bool(state is not None and state.active)

    def _require_features(self) -> FeatureRegistry:
        if self.features is None:
            raise RuntimeError("The Remi feature registry is not configured")
        return self.features

    @staticmethod
    def state(context):
        return context.window_manager.remi_session

    def object(self, context):
        state = self.state(context)
        if not state.active or not state.session_id:
            return None
        return self.objects.find(state.session_id)

    @staticmethod
    def _remove_object(obj):
        SessionObjectStore.remove(obj)

    @staticmethod
    def _object_exists(obj):
        return SessionObjectStore.exists(obj)

    def _create_working_copy(self, context, source, label: str, *, linked=True):
        """Give a stage its own mesh; the locked source remains read-only."""
        return self.objects.create_working_copy(
            context,
            source,
            label,
            linked=linked,
        )

    @staticmethod
    def _select_only(context, obj):
        SessionObjectStore.select_only(context, obj)

    def _require_disk(self):
        if self.disk is None:
            raise RuntimeError("The Remi session disk service is not initialized")
        return self.disk

    def _snapshot(self, obj, slot: str):
        """Write one named recovery slot through the session disk service."""
        self._require_disk().write(obj, slot)

    def _append_checkpoint_object(self, context, slot: str):
        return self.objects.append_checkpoint(
            self._require_disk(),
            slot,
            expected_name=self.state(context).object_name,
        )

    def _load_checkpoint(self, context, slot: str):
        """Replace the session object with the object in one recovery slot."""
        current = self.object(context)
        if not current:
            raise RuntimeError("The Remi working object is missing")
        state = self.state(context)
        return self.objects.load_checkpoint(
            context,
            self._require_disk(),
            slot,
            current=current,
            session_id=state.session_id,
            expected_name=state.object_name,
        )

    def load_source_temporary(self, context):
        """Load the source checkpoint as a disposable object for texture baking."""
        return self.objects.load_temporary_source(
            context,
            self._require_disk(),
            expected_name=self.state(context).object_name,
        )

    def cleanup_temporary_source(self, source, dependencies_before):
        """Discard an unused checkpoint load and all dependencies it introduced."""
        self.objects.cleanup_temporary_source(source, dependencies_before)

    def _update_stats(self, context, message=None):
        state = self.state(context)
        obj = self.object(context)
        if obj:
            state.object_name = obj.name
            state.current_vertices = len(obj.data.vertices)
            state.current_faces = len(obj.data.polygons)
        if message is not None:
            state.status = message
        state.checkpoint_megabytes = (
            self.disk.total_megabytes() if self.disk is not None else 0.0
        )

    def begin(self, context, obj):
        state = self.state(context)
        if state.active:
            raise RuntimeError("A Remi session is already active")
        if not obj or obj.type != "MESH" or context.mode != "OBJECT":
            raise RuntimeError("Select one mesh in Object Mode")
        if obj.library:
            raise RuntimeError("Linked meshes must be made local before starting Remi")

        session_id = uuid.uuid4().hex
        disk = SessionDiskService.create(session_id)
        obj[SESSION_ID_KEY] = session_id
        self.disk = disk
        try:
            self._snapshot(obj, "source")
        except Exception:
            obj.pop(SESSION_ID_KEY, None)
            disk.close()
            self.disk = None
            raise

        self.pending_command = ""
        self.last_step_label = ""
        self.previous_step_label = ""
        self.previous_stage = "REPAIR"
        self.redo_step_label = ""
        self.redo_stage = "REPAIR"
        self.pending_step_label = ""
        self.pending_stage = "REPAIR"

        state.active = True
        state.busy = False
        state.interactive = False
        state.session_id = session_id
        state.object_name = obj.name
        state.status = "Ready"
        state.current_step = "Source"
        state.step_index = 0
        state.can_undo = False
        state.can_redo = False
        state.source_faces = len(obj.data.polygons)
        state.stage = "REPAIR"
        self._select_only(context, obj)
        self._update_stats(context)

    def ensure_active_object(self, context):
        obj = self.object(context)
        if not obj:
            return False
        state = self.state(context)
        if state.interactive and self._object_exists(self.interactive_copy):
            return True
        if context.view_layer.objects.active != obj or not obj.select_get():
            self._select_only(context, obj)
        return True

    def queue(self, context, command: str):
        state = self.state(context)
        if not state.active:
            raise RuntimeError("No Remi session is active")
        if state.interactive:
            raise RuntimeError("Finish or cancel the active interactive tool first")
        if state.busy or self.pending_command:
            raise RuntimeError("Remi is already processing a command")
        self.pending_command = command
        registered = self._require_features().action(command)
        if registered is not None:
            source = self.object(context)
            state.busy = True
            execution = FeatureExecutionContext(
                blender_context=context,
                source=source,
                working_copy=None,
                disk=self._require_disk(),
            )
            state.status = registered.feature.queued_message(
                registered.action,
                execution,
            )

    def pop_command(self):
        command = self.pending_command
        self.pending_command = ""
        return command

    def execute_action(self, context, registered: RegisteredAction, *, payload=None):
        """Run one injected action inside the session-owned checkpoint transaction."""
        started = time.perf_counter()
        state = self.state(context)
        action = registered.action
        feature = registered.feature
        candidate = None
        working_copy = None
        source_checkpoint = None
        dependencies_before = None
        try:
            # No feature code runs before this validated mesh checkpoint exists.
            self._prepare_step(context, action.name)
            current = self.object(context)
            working_copy = self._create_working_copy(context, current, action.name)
            if action.requires_source_checkpoint:
                state.status = f"Loading the source checkpoint for {action.name.lower()}…"
                source_checkpoint, dependencies_before = self.load_source_temporary(context)
            state.status = f"Running {action.name.lower()}…"
            execution = FeatureExecutionContext(
                blender_context=context,
                source=current,
                working_copy=working_copy,
                source_checkpoint=source_checkpoint,
                disk=self._require_disk(),
                payload=payload or {},
            )
            result = feature.execute(action, execution)
            candidate = result.candidate
            report = result.report
            if candidate != working_copy and self._object_exists(working_copy):
                self._remove_object(working_copy)
                working_copy = None
            self._commit_step(
                context,
                candidate,
                action.name,
                next_stage=registered.next_feature,
            )
            elapsed = time.perf_counter() - started
            duration = f"{elapsed:.1f}s" if elapsed < 60.0 else f"{elapsed / 60.0:.1f} min"
            self._update_stats(context, f"{action.name} complete in {duration}")
            return self.object(context), report
        except Exception as exc:
            for disposable in (candidate, working_copy):
                if (
                    self._object_exists(disposable)
                    and disposable != self.object(context)
                ):
                    self._remove_object(disposable)
            self._abandon_step(context, None, f"{action.name} failed: {exc}")
            raise
        finally:
            if dependencies_before is not None:
                self.cleanup_temporary_source(source_checkpoint, dependencies_before)

    def start_interactive_action(self, context, registered: RegisteredAction):
        """Checkpoint first, then open an injected interactive action."""
        state = self.state(context)
        action = registered.action
        feature = registered.feature
        working_copy = None
        try:
            self._prepare_step(context, action.name)
            state.status = f"Building the {action.name.lower()} workspace…"
            current = self.object(context)
            working_copy = self._create_working_copy(
                context,
                current,
                action.name,
                linked=False,
            )
            self.interactive_copy = working_copy
            feature.start_interactive(
                action,
                FeatureExecutionContext(
                    blender_context=context,
                    source=current,
                    working_copy=working_copy,
                    disk=self._require_disk(),
                ),
            )
            state.interactive = True
            state.busy = False
            state.status = f"{action.name} active"
        except Exception as exc:
            try:
                feature.cancel_interactive(action)
            except Exception:
                pass
            if self._object_exists(working_copy):
                self._remove_object(working_copy)
            self.interactive_copy = None
            current = self.object(context)
            if current:
                self._select_only(context, current)
            self._abandon_step(context, None, f"{action.name} failed: {exc}")
            raise

    def execute_manual_repair(self, context, ring_world, ring_normals=None):
        return self.execute_action(
            context,
            self._require_features().require_action("MANUAL_REPAIR"),
            payload={"ring_world": ring_world, "ring_normals": ring_normals},
        )

    def commit_interactive_step(self, context, candidate, label, next_stage):
        if self._object_exists(self.interactive_copy) and self.interactive_copy != candidate:
            self._remove_object(self.interactive_copy)
        self.interactive_copy = None
        self._commit_step(context, candidate, label, next_stage=next_stage)
        self.state(context).interactive = False

    def abandon_interactive_step(self, context, message):
        if self._object_exists(self.interactive_copy):
            self._remove_object(self.interactive_copy)
        self.interactive_copy = None
        current = self.object(context)
        if current:
            self._select_only(context, current)
        self._abandon_step(context, None, message)
        self.state(context).interactive = False

    def _prepare_step(self, context, label: str):
        state = self.state(context)
        current = self.object(context)
        if not current:
            raise RuntimeError("The Remi working object is missing")
        state.busy = True
        state.status = f"Saving recovery point before {label.lower()}…"
        self._snapshot(current, "pending")
        self.last_step_label = label
        self.pending_step_label = state.current_step
        self.pending_stage = state.stage

    def _abandon_step(self, context, candidate=None, message="Step failed"):
        if candidate and candidate != self.object(context):
            self._remove_object(candidate)
        if self.disk is not None:
            self.disk.discard("pending")
        state = self.state(context)
        state.busy = False
        current = self.object(context)
        if current:
            self._select_only(context, current)
        self._update_stats(context, message)

    def _commit_step(self, context, candidate, label: str, next_stage=None):
        state = self.state(context)
        current = self.object(context)
        if not current or not candidate or candidate.type != "MESH":
            raise RuntimeError("Remi did not produce a valid mesh result")
        if candidate == current:
            raise RuntimeError("Remi cannot commit the working object as its own candidate")

        old_name = current.name
        old_collections = list(current.users_collection)
        old_dependencies = self.objects.object_dependencies(current)
        candidate_collections = set(candidate.users_collection)
        for collection in old_collections:
            if collection not in candidate_collections:
                collection.objects.link(candidate)
        for collection in list(candidate.users_collection):
            if collection not in old_collections:
                collection.objects.unlink(candidate)

        self._remove_object(current)
        self.objects.remove_orphan_dependencies(old_dependencies)
        candidate.name = old_name
        candidate[SESSION_ID_KEY] = state.session_id
        self._select_only(context, candidate)

        disk = self._require_disk()
        disk.promote("pending", "previous")
        disk.discard("redo")

        state.busy = False
        state.current_step = label
        state.step_index += 1
        state.can_undo = True
        state.can_redo = False
        self.previous_step_label = self.pending_step_label or "Source"
        self.previous_stage = self.pending_stage
        self.redo_step_label = ""
        self.redo_stage = "REPAIR"
        if next_stage is not None:
            state.stage = next_stage
        else:
            # Compatibility for interactive consumers that began their
            # transaction before returning a candidate.
            state.stage = {
                "Repair": "REMESH",
                "Remesh": "RETOPOLOGY",
                "Decimate": "RETOPOLOGY",
                "Retopology": "UV",
                "Auto Retopology": "UV",
                "UV": "BAKE",
            }.get(label, "BAKE" if label.startswith("Bake") else state.stage)
        self._update_stats(context, f"{label} complete")

    def undo(self, context):
        state = self.state(context)
        disk = self._require_disk()
        if not state.can_undo or not disk.exists("previous"):
            raise RuntimeError("There is no previous Remi step")
        current = self.object(context)
        self.redo_step_label = state.current_step
        self.redo_stage = state.stage
        self._snapshot(current, "redo")
        self._load_checkpoint(context, "previous")
        disk.discard("previous")
        state.step_index = max(0, state.step_index - 1)
        state.can_undo = False
        state.can_redo = True
        state.current_step = self.previous_step_label or "Source"
        state.stage = self.previous_stage
        self.previous_step_label = ""
        self._update_stats(context, "Returned to the previous committed mesh")

    def redo(self, context):
        state = self.state(context)
        disk = self._require_disk()
        if not state.can_redo or not disk.exists("redo"):
            raise RuntimeError("There is no Remi step to redo")
        current = self.object(context)
        self.previous_step_label = state.current_step
        self.previous_stage = state.stage
        self._snapshot(current, "previous")
        self._load_checkpoint(context, "redo")
        disk.discard("redo")
        state.step_index += 1
        state.can_undo = True
        state.can_redo = False
        state.current_step = self.redo_step_label or self.last_step_label or "Result"
        state.stage = self.redo_stage
        self.redo_step_label = ""
        self._update_stats(context, "Restored the next committed mesh")

    def reset(self, context):
        state = self.state(context)
        current = self.object(context)
        if not current:
            raise RuntimeError("The Remi working object is missing")
        self.redo_step_label = state.current_step
        self.redo_stage = state.stage
        self._snapshot(current, "redo")
        self._load_checkpoint(context, "source")
        self._require_disk().discard("previous")
        state.current_step = "Source"
        state.stage = "REPAIR"
        state.step_index = 0
        state.can_undo = False
        state.can_redo = True
        self.previous_step_label = ""
        self._update_stats(context, "Restored the source mesh")

    def _clear_state(self, context):
        state = self.state(context)
        state.active = False
        state.busy = False
        state.interactive = False
        state.session_id = ""
        state.object_name = ""
        state.status = ""
        state.current_step = "Source"
        state.step_index = 0
        state.can_undo = False
        state.can_redo = False
        state.source_faces = 0
        state.current_faces = 0
        state.current_vertices = 0
        state.checkpoint_megabytes = 0.0

    def _clear_cache(self):
        if self._object_exists(self.interactive_copy):
            self._remove_object(self.interactive_copy)
        self.interactive_copy = None
        if self.disk is not None:
            self.disk.close()
        self.disk = None
        self.pending_command = ""
        self.last_step_label = ""
        self.previous_step_label = ""
        self.previous_stage = "REPAIR"
        self.redo_step_label = ""
        self.redo_stage = "REPAIR"
        self.pending_step_label = ""
        self.pending_stage = "REPAIR"

    def finish(self, context):
        obj = self.object(context)
        if not obj:
            raise RuntimeError("The Remi working object is missing")
        obj.pop(SESSION_ID_KEY, None)
        self._select_only(context, obj)
        self._clear_cache()
        self._clear_state(context)
        return obj

    def cancel(self, context):
        state = self.state(context)
        if not state.active:
            return None
        obj = self._load_checkpoint(context, "source")
        obj.pop(SESSION_ID_KEY, None)
        self._select_only(context, obj)
        self._clear_cache()
        self._clear_state(context)
        return obj


runtime = RemiSessionRuntime()


def register():
    preserved = [runtime.disk.directory] if runtime.disk is not None else []
    removed = SessionDiskService.cleanup_abandoned(preserve=preserved)
    if removed:
        print(f"Remi: cleaned {len(removed)} abandoned disk session(s)")
    bpy.utils.register_class(RemiSessionState)
    bpy.types.WindowManager.remi_session = bpy.props.PointerProperty(type=RemiSessionState)
    from . import session_operators

    session_operators.register()


def unregister():
    try:
        if hasattr(bpy.context.window_manager, "remi_session") and bpy.context.window_manager.remi_session.active:
            runtime.cancel(bpy.context)
    except Exception:
        pass
    finally:
        runtime._clear_cache()
    from . import session_operators

    session_operators.unregister()
    if hasattr(bpy.types.WindowManager, "remi_session"):
        del bpy.types.WindowManager.remi_session
    bpy.utils.unregister_class(RemiSessionState)
