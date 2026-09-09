"""Small reusable defaults for workflow feature adapters."""

from __future__ import annotations

from typing import Any

from ..workflow.contracts import (
    FeatureAction,
    FeatureExecutionContext,
    FeatureUIContext,
    StageResult,
)


def stage_result(raw_result) -> StageResult:
    """Normalize the existing mesh mechanisms at the feature boundary."""
    candidate, error, report = raw_result
    if error:
        raise RuntimeError(error)
    return StageResult(candidate=candidate, report=report or {})


class FeatureDefaults:
    """Defaults shared by features while each feature owns its orchestration."""

    def draw(self, layout: Any, context: FeatureUIContext) -> None:
        # Feature-owned controls are moved behind this seam in a later slice.
        return None

    def queued_message(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> str:
        return f"Preparing {action.name.lower()}…"

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        raise RuntimeError(f"{action.name} is not an atomic action")

    def start_interactive(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> None:
        raise RuntimeError(f"{action.name} is not an interactive action")

    def cancel_interactive(self, action: FeatureAction) -> None:
        return None
