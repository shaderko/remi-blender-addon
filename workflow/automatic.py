"""Execute a frozen plan through existing session transactions, one stage per tick."""

from .presets import apply_settings, capture_settings, checked_settings, validate_actions, validate_document


class AutomaticFlow:
    def __init__(self, session, features):
        self.session = session
        self.features = features
        self.actions = ()
        self.settings = {}
        self.index = 0
        self.prepared = False
        self.stop_requested = False
        self.name = ""

    def start(self, context, document):
        validate_document(document)
        if self.session.state(context).active:
            raise RuntimeError("Finish or cancel the current Remi session before starting a full flow")
        validate_actions(self.features, document["actions"])
        settings = context.scene.remi_settings
        frozen = checked_settings(settings, document["settings"])
        previous = capture_settings(settings)
        try:
            apply_settings(settings, frozen)
            for identifier in document["actions"]:
                registered = self.features.require_action(identifier)
                registered.feature.preflight_automatic(registered.action, context)
            self.session.begin(context, context.active_object)
        except Exception:
            apply_settings(settings, previous)
            raise
        self.actions = tuple(document["actions"])
        self.settings = frozen
        self.name = document["name"]
        self.index = 0
        self.prepared = False
        self.stop_requested = False
        state = self.session.state(context)
        state.automatic = True
        state.busy = True
        state.flow_total = len(self.actions)
        state.flow_completed = 0
        state.status = f"{self.name}: ready to run {len(self.actions)} stages"

    def tick(self, context):
        """Return RUNNING, FINISHED, STOPPED or FAILED. Never skip a failed stage."""
        state = self.session.state(context)
        if not state.active:
            return "STOPPED"
        if self.stop_requested or state.flow_stop_requested:
            state.automatic = False
            state.busy = False
            state.status = "Full flow stopped. Review the result, continue manually, or cancel to restore the source."
            return "STOPPED"
        if self.index >= len(self.actions):
            result = self.session.finish(context)
            context.scene.remi_flow.last_result = f"{self.name} finished · {len(self.actions)} stages · {result.name}"
            return "FINISHED"
        registered = self.features.require_action(self.actions[self.index])
        state.stage = registered.feature_id
        state.busy = True
        state.flow_completed = self.index
        if not self.prepared:
            state.status = f"{self.index + 1}/{len(self.actions)} · {registered.action.name} next"
            self.prepared = True
            return "RUNNING"
        try:
            # Saved plans are immutable for the duration of the run, even if a
            # different Blender panel edits a scene setting between timer ticks.
            apply_settings(context.scene.remi_settings, self.settings)
            self.session.execute_action(context, registered)
            self.index += 1
            state.flow_completed = self.index
            state.busy = True
            self.prepared = False
            return "RUNNING"
        except Exception as exc:
            state.automatic = False
            state.busy = False
            state.status = f"Stopped at {registered.action.name}: {exc}"
            context.scene.remi_flow.last_result = state.status
            return "FAILED"
