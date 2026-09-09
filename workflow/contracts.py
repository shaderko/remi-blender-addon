"""Shared contracts for injected Remi workflow features."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol


class ExecutionMode(str, Enum):
    ATOMIC = "ATOMIC"
    INTERACTIVE = "INTERACTIVE"


@dataclass(frozen=True)
class FeatureAction:
    id: str
    name: str
    description: str = ""
    mode: ExecutionMode = ExecutionMode.ATOMIC
    requires_source_checkpoint: bool = False
    next_feature: str | None = None


@dataclass(frozen=True)
class FeatureDescriptor:
    id: str
    name: str
    icon: str
    next_feature: str | None
    actions: tuple[FeatureAction, ...]


@dataclass(frozen=True)
class FeatureUIContext:
    blender_context: Any
    state: Any


@dataclass(frozen=True)
class FeatureExecutionContext:
    blender_context: Any
    source: Any
    working_copy: Any
    source_checkpoint: Any = None
    disk: Any = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageResult:
    candidate: Any
    report: Mapping[str, Any] = field(default_factory=dict)


class WorkflowFeature(Protocol):
    descriptor: FeatureDescriptor

    def scene_settings(self) -> Mapping[str, Any]:
        """Return Blender scene properties owned by this feature."""

    def blender_classes(self) -> tuple[type, ...]:
        """Return Blender types registered for this feature."""

    def draw(self, layout: Any, context: FeatureUIContext) -> None:
        """Draw controls owned by this feature."""

    def queued_message(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> str:
        """Describe work before the action begins."""

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        """Build a candidate without committing session state."""

    def start_interactive(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> None:
        """Start an interactive action on a session-provided working copy."""

    def cancel_interactive(self, action: FeatureAction) -> None:
        """Release feature-owned interactive state."""
