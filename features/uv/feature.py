"""UV workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, session_command, stage_result
from ..contracts import UVUseCases
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

    def __init__(self, service: UVUseCases):
        self._service = service

    def scene_settings(self):
        from .settings import SCENE_SETTINGS

        return SCENE_SETTINGS

    def blender_classes(self) -> tuple[type, ...]:
        from .operators import Remi_OT_GenerateUV

        return (Remi_OT_GenerateUV,)

    def draw(self, layout, context) -> None:
        settings = context.blender_context.scene.remi_settings
        layout.label(text="UV", icon="UV")
        layout.prop(settings, "bake_uv_profile", text="Profile")
        row = layout.row(align=True)
        row.prop(settings, "bake_texture_size", text="Texture")
        row.prop(settings, "bake_uv_margin_px", text="Gap")
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
            return stage_result(
                self._service.generate(
                    context.source,
                    context.blender_context.scene.remi_settings,
                    candidate=context.working_copy,
                )
            )
        return super().execute(action, context)
