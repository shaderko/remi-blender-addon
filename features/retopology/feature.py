"""Interactive and automatic retopology workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, stage_result
from ...workflow.contracts import (
    ExecutionMode,
    FeatureAction,
    FeatureDescriptor,
    FeatureExecutionContext,
    StageResult,
)


class RetopologyFeature(FeatureDefaults):
    descriptor = FeatureDescriptor(
        id="RETOPOLOGY",
        name="Retopology",
        icon="MOD_TRIANGULATE",
        next_feature="UV",
        actions=(
            FeatureAction(
                "INSTANT_START",
                "Interactive Retopology",
                "Open the Instant Meshes retopology workspace",
                mode=ExecutionMode.INTERACTIVE,
            ),
            FeatureAction(
                "AUTO_RETOPO",
                "Auto Retopology",
                "Run the external AutoRemesher",
            ),
        ),
    )

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        if action.id == "AUTO_RETOPO":
            from ...operators import _create_autoremesher_candidate

            return stage_result(
                _create_autoremesher_candidate(
                    context.working_copy,
                    context.blender_context.scene.remi_settings,
                    disk=context.disk,
                )
            )
        return super().execute(action, context)

    def start_interactive(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> None:
        if action.id != "INSTANT_START":
            return super().start_interactive(action, context)
        from ...instant_meshes.runtime import runtime

        runtime.start(
            context.working_copy,
            context.blender_context.scene.remi_instant_meshes,
        )

    def cancel_interactive(self, action: FeatureAction) -> None:
        if action.id == "INSTANT_START":
            from ...instant_meshes.runtime import runtime

            runtime.shutdown()
