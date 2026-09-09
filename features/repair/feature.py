"""Repair workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, stage_result
from ...workflow.contracts import (
    FeatureAction,
    FeatureDescriptor,
    FeatureExecutionContext,
    StageResult,
)


class RepairFeature(FeatureDefaults):
    descriptor = FeatureDescriptor(
        id="REPAIR",
        name="Repair",
        icon="TOOL_SETTINGS",
        next_feature="REMESH",
        actions=(
            FeatureAction(
                "REPAIR",
                "Repair",
                "Repair holes and fragmented surfaces",
            ),
            FeatureAction(
                "MANUAL_REPAIR",
                "Manual Repair",
                "Patch a user-selected surface region",
                next_feature="REPAIR",
            ),
        ),
    )

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        settings = context.blender_context.scene.remi_settings
        if action.id == "REPAIR":
            from ...operators import _create_repair_candidate

            return stage_result(
                _create_repair_candidate(
                    context.source,
                    settings,
                    candidate=context.working_copy,
                    disk=context.disk,
                )
            )
        if action.id == "MANUAL_REPAIR":
            from ...operators import _create_surface_ring_patch

            return stage_result(
                _create_surface_ring_patch(
                    context.source,
                    settings,
                    context.payload["ring_world"],
                    ring_normals=context.payload.get("ring_normals"),
                    result=context.working_copy,
                )
            )
        return super().execute(action, context)
