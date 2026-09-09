"""UV workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, stage_result
from ...workflow.contracts import (
    FeatureAction,
    FeatureDescriptor,
    FeatureExecutionContext,
    StageResult,
)


class UVFeature(FeatureDefaults):
    descriptor = FeatureDescriptor(
        id="UV",
        name="UV",
        icon="UV",
        next_feature="BAKE",
        actions=(FeatureAction("UV", "UV", "Generate and inspect UVs"),),
    )

    def queued_message(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> str:
        return (
            f"Generating UVs for {len(context.source.data.polygons):,} faces… "
            "Blender stays busy while the atlas is built."
        )

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        if action.id == "UV":
            from ...operators import _create_uv_candidate

            return stage_result(
                _create_uv_candidate(
                    context.source,
                    context.blender_context.scene.remi_settings,
                    candidate=context.working_copy,
                )
            )
        return super().execute(action, context)
