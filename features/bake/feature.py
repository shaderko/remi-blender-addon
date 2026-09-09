"""Texture bake workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, stage_result
from ...workflow.contracts import (
    FeatureAction,
    FeatureDescriptor,
    FeatureExecutionContext,
    StageResult,
)


class BakeFeature(FeatureDefaults):
    descriptor = FeatureDescriptor(
        id="BAKE",
        name="Bake",
        icon="RENDER_STILL",
        next_feature="BAKE",
        actions=(
            FeatureAction(
                "BAKE_ALL",
                "Bake All Maps",
                "Bake albedo, roughness, normal, and ambient occlusion",
                requires_source_checkpoint=True,
            ),
            FeatureAction(
                "BAKE_DIFFUSE",
                "Bake Albedo",
                requires_source_checkpoint=True,
            ),
            FeatureAction(
                "BAKE_ROUGHNESS",
                "Bake Roughness",
                requires_source_checkpoint=True,
            ),
            FeatureAction(
                "BAKE_NORMAL",
                "Bake Normal",
                requires_source_checkpoint=True,
            ),
            FeatureAction(
                "BAKE_AO",
                "Bake AO",
                requires_source_checkpoint=True,
            ),
        ),
    )

    _PASSES = {
        "BAKE_ALL": ("diffuse", "roughness", "normal", "ao"),
        "BAKE_DIFFUSE": ("diffuse",),
        "BAKE_ROUGHNESS": ("roughness",),
        "BAKE_NORMAL": ("normal",),
        "BAKE_AO": ("ao",),
    }

    def queued_message(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> str:
        size = context.blender_context.scene.remi_settings.bake_texture_size
        return (
            f"{action.name} at {size}×{size}… "
            "Blender stays busy while Cycles bakes."
        )

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        passes = self._PASSES.get(action.id)
        if passes is None:
            return super().execute(action, context)
        from ...operators import _create_bake_candidate

        return stage_result(
            _create_bake_candidate(
                context.source_checkpoint,
                context.source,
                context.blender_context.scene.remi_settings,
                passes=passes,
                name_prefix=context.source.name,
                candidate=context.working_copy,
            )
        )
