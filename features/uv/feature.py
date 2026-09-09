"""UV workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, session_command, stage_result
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

    def draw(self, layout, context) -> None:
        settings = context.blender_context.scene.remi_settings
        layout.label(text="UV", icon="UV")
        layout.prop(settings, "bake_uv_profile", text="Profile")
        row = layout.row(align=True)
        row.prop(settings, "bake_texture_size", text="Texture")
        row.prop(settings, "bake_uv_margin_px", text="Padding")
        layout.prop(settings, "bake_uv_preserve_seams", text="Preserve Marked Seams")
        layout.separator()
        action = layout.column()
        action.scale_y = 1.35
        session_command(action, "UV", "Generate UV", "UV")

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
