"""Narrow injectable service contracts used by workflow feature objects."""

from typing import Any, Protocol


class RepairUseCases(Protocol):
    def repair(self, source: Any, settings: Any, *, candidate: Any, disk: Any): ...

    def manual_patch(
        self,
        source: Any,
        settings: Any,
        ring_world: Any,
        *,
        ring_normals: Any,
        candidate: Any,
    ): ...


class RemeshUseCases(Protocol):
    def remesh(self, source: Any, settings: Any, *, candidate: Any, disk: Any): ...

    def decimate(self, candidate: Any, settings: Any, *, disk: Any): ...


class RetopologyUseCases(Protocol):
    def automatic(self, candidate: Any, settings: Any, *, disk: Any): ...

    def start_interactive(self, candidate: Any, settings: Any) -> None: ...

    def cancel_interactive(self) -> None: ...


class UVUseCases(Protocol):
    def generate(self, source: Any, settings: Any, *, candidate: Any): ...


class BakeUseCases(Protocol):
    def bake(
        self,
        source_checkpoint: Any,
        current: Any,
        settings: Any,
        *,
        passes: tuple[str, ...],
        name_prefix: str,
        candidate: Any,
    ): ...
