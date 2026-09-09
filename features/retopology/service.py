"""Composition of automatic and interactive retopology use cases."""


class RetopologyService:
    def __init__(self, automatic, interactive):
        self._automatic = automatic
        self._interactive = interactive

    def automatic(self, candidate, settings, *, disk=None):
        return self._automatic.create_candidate(candidate, settings, disk=disk)

    def start_interactive(self, candidate, settings):
        self._interactive.start(candidate, settings)

    def cancel_interactive(self):
        self._interactive.shutdown()
