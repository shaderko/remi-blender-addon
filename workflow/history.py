"""Bounded checkpoint transitions for the current Remi mesh."""

from __future__ import annotations

from dataclasses import dataclass

from .disk import SESSION_ID_KEY


@dataclass
class _HistoryLabels:
    last_step: str = ""
    previous_step: str = ""
    previous_stage: str = "REPAIR"
    redo_step: str = ""
    redo_stage: str = "REPAIR"
    pending_step: str = ""
    pending_stage: str = "REPAIR"


class SessionHistory:
    """Own one-step Back/Redo metadata and checkpoint state transitions."""

    def __init__(self, objects):
        self.objects = objects
        self.labels = _HistoryLabels()

    def reset_labels(self):
        self.labels = _HistoryLabels()

    def prepare(self, state, disk, current, label: str):
        if not current:
            raise RuntimeError("The Remi working object is missing")
        state.busy = True
        state.status = f"Saving recovery point before {label.lower()}…"
        disk.write(current, "pending")
        self.labels.last_step = label
        self.labels.pending_step = state.current_step
        self.labels.pending_stage = state.stage

    def abandon(self, context, state, disk, current, candidate=None):
        if candidate and candidate != current:
            self.objects.remove(candidate)
        if disk is not None:
            disk.discard("pending")
        state.busy = False
        if current:
            self.objects.select_only(context, current)

    def commit(
        self,
        context,
        state,
        disk,
        current,
        candidate,
        label: str,
        *,
        next_stage=None,
    ):
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

        self.objects.remove(current)
        self.objects.remove_orphan_dependencies(old_dependencies)
        candidate.name = old_name
        candidate[SESSION_ID_KEY] = state.session_id
        self.objects.select_only(context, candidate)

        disk.promote("pending", "previous")
        disk.discard("redo")

        state.busy = False
        state.current_step = label
        state.step_index += 1
        state.can_undo = True
        state.can_redo = False
        self.labels.previous_step = self.labels.pending_step or "Source"
        self.labels.previous_stage = self.labels.pending_stage
        self.labels.redo_step = ""
        self.labels.redo_stage = "REPAIR"
        if next_stage is not None:
            state.stage = next_stage
        else:
            state.stage = {
                "Repair": "REMESH",
                "Remesh": "RETOPOLOGY",
                "Decimate": "RETOPOLOGY",
                "Retopology": "UV",
                "Auto Retopology": "UV",
                "UV": "BAKE",
            }.get(label, "BAKE" if label.startswith("Bake") else state.stage)
        return candidate

    def undo(self, context, state, disk, current):
        if not state.can_undo or not disk.exists("previous"):
            raise RuntimeError("There is no previous Remi step")
        self.labels.redo_step = state.current_step
        self.labels.redo_stage = state.stage
        disk.write(current, "redo")
        restored = self.objects.load_checkpoint(
            context,
            disk,
            "previous",
            current=current,
            session_id=state.session_id,
            expected_name=state.object_name,
        )
        disk.discard("previous")
        state.step_index = max(0, state.step_index - 1)
        state.can_undo = False
        state.can_redo = True
        state.current_step = self.labels.previous_step or "Source"
        state.stage = self.labels.previous_stage
        self.labels.previous_step = ""
        return restored

    def redo(self, context, state, disk, current):
        if not state.can_redo or not disk.exists("redo"):
            raise RuntimeError("There is no Remi step to redo")
        self.labels.previous_step = state.current_step
        self.labels.previous_stage = state.stage
        disk.write(current, "previous")
        restored = self.objects.load_checkpoint(
            context,
            disk,
            "redo",
            current=current,
            session_id=state.session_id,
            expected_name=state.object_name,
        )
        disk.discard("redo")
        state.step_index += 1
        state.can_undo = True
        state.can_redo = False
        state.current_step = self.labels.redo_step or self.labels.last_step or "Result"
        state.stage = self.labels.redo_stage
        self.labels.redo_step = ""
        return restored

    def restore_source(self, context, state, disk, current):
        if not current:
            raise RuntimeError("The Remi working object is missing")
        self.labels.redo_step = state.current_step
        self.labels.redo_stage = state.stage
        disk.write(current, "redo")
        restored = self.objects.load_checkpoint(
            context,
            disk,
            "source",
            current=current,
            session_id=state.session_id,
            expected_name=state.object_name,
        )
        disk.discard("previous")
        state.current_step = "Source"
        state.stage = "REPAIR"
        state.step_index = 0
        state.can_undo = False
        state.can_redo = True
        self.labels.previous_step = ""
        return restored
