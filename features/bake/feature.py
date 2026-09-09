"""Texture bake workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, session_command, stage_result
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

    def draw(self, layout, context) -> None:
        settings = context.blender_context.scene.remi_settings
        layout.label(text="Bake", icon="RENDER_STILL")
        layout.label(text="Uses original source checkpoint", icon="LOCKED")
        layout.prop(settings, "bake_texture_size", text="Texture Size")
        layout.prop(settings, "bake_auto_unwrap", text="Auto Unwrap")
        if settings.bake_auto_unwrap:
            row = layout.row(align=True)
            row.prop(settings, "bake_uv_method", text="UV Method")
            if settings.bake_uv_method == "REMI":
                row.prop(settings, "bake_uv_profile", text="Profile")
                layout.prop(settings, "bake_uv_margin_px", text="Padding")
            else:
                layout.prop(settings, "bake_uv_island_margin", text="Margin")
        row = layout.row(align=True)
        row.prop(settings, "bake_recalc_normals", text="Recalc Normals")
        row.prop(settings, "bake_half_scale", text="Half Scale")
        row = layout.row(align=True)
        row.prop(settings, "bake_cage_extrusion", text="Cage")
        row.prop(settings, "bake_max_ray_distance", text="Max Ray")
        layout.separator()
        action = layout.column(align=True)
        action.scale_y = 1.3
        session_command(action, "BAKE_ALL", "Bake All Maps", "RENDER_STILL")
        row = action.row(align=True)
        session_command(row, "BAKE_DIFFUSE", "Albedo")
        session_command(row, "BAKE_ROUGHNESS", "Roughness")
        row = action.row(align=True)
        session_command(row, "BAKE_NORMAL", "Normal")
        session_command(row, "BAKE_AO", "AO")

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
        from .service import create_candidate

        return stage_result(
            create_candidate(
                context.source_checkpoint,
                context.source,
                context.blender_context.scene.remi_settings,
                passes=passes,
                name_prefix=context.source.name,
                candidate=context.working_copy,
            )
        )
