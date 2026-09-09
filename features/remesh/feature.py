"""Remesh and reduction workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, stage_result
from ...workflow.contracts import (
    FeatureAction,
    FeatureDescriptor,
    FeatureExecutionContext,
    StageResult,
)


class RemeshFeature(FeatureDefaults):
    descriptor = FeatureDescriptor(
        id="REMESH",
        name="Remesh",
        icon="MOD_REMESH",
        next_feature="RETOPOLOGY",
        actions=(
            FeatureAction("REMESH", "Remesh", "Create a clean watertight surface"),
            FeatureAction("DECIMATE", "Decimate", "Reduce the mesh with MeshLab"),
        ),
    )

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        settings = context.blender_context.scene.remi_settings
        if action.id == "REMESH":
            from ...operators import _create_sdf_candidate

            return stage_result(
                _create_sdf_candidate(
                    context.source,
                    settings,
                    apply_result=True,
                    candidate=context.working_copy,
                    disk=context.disk,
                )
            )
        if action.id == "DECIMATE":
            from ...operators import _create_decimate_candidate

            return stage_result(
                _create_decimate_candidate(
                    context.working_copy,
                    settings,
                    disk=context.disk,
                )
            )
        return super().execute(action, context)
