"""Remesh and reduction workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, session_command, stage_result
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

    def scene_settings(self):
        from .settings import SCENE_SETTINGS

        return SCENE_SETTINGS

    def blender_classes(self) -> tuple[type, ...]:
        from .operators import Remi_OT_ApplyRemesh, Remi_OT_Decimate, Remi_OT_SDFRemesh

        return Remi_OT_SDFRemesh, Remi_OT_ApplyRemesh, Remi_OT_Decimate

    def draw(self, layout, context) -> None:
        settings = context.blender_context.scene.remi_settings
        layout.label(text="Remesh", icon="MOD_NORMALEDIT")
        layout.prop(settings, "remesh_backend", text="Method")
        layout.prop(settings, "voxel_size", text="Voxel Size")
        if settings.remesh_backend == "VOLUME":
            layout.label(text="Closes gaps and fits back to the surface", icon="INFO")
            row = layout.row(align=True)
            row.prop(settings, "hole_close_ratio", text="Crack Size")
            row.prop(settings, "volume_guide_voxel_scale", text="Resolution")
            layout.prop(settings, "volume_surface_fit_ratio", text="Surface Fit Reach")
            layout.prop(settings, "volume_preserve_features", text="Preserve Sharp Creases")
            if settings.volume_preserve_features:
                row = layout.row(align=True)
                row.prop(settings, "volume_feature_angle", text="Feature")
                row.prop(settings, "volume_feature_reach", text="Reach")
        else:
            row = layout.row(align=True)
            row.prop(settings, "use_sdf_fillet", text="Fillet")
            row.prop(settings, "use_sdf_smoothing", text="Smooth")
            if settings.use_sdf_fillet:
                layout.prop(settings, "fillet_radius", text="Fillet Radius")
            if settings.use_sdf_smoothing:
                layout.prop(settings, "smoothing_iterations", text="Smooth Steps")

        layout.separator()
        action = layout.column()
        action.scale_y = 1.35
        session_command(action, "REMESH", "Run Remesh", "MOD_NORMALEDIT")

        layout.separator()
        decimate = layout.box()
        decimate.label(text="Optional · Reduce Faces", icon="MOD_DECIM")
        row = decimate.row(align=True)
        row.prop(settings, "decimation_passes", text="Passes")
        row.prop(settings, "target_percentage", text="Keep")
        row = decimate.row(align=True)
        row.prop(settings, "decimation_preserve_detail", text="Preserve Detail")
        row.prop(settings, "decimation_with_texture", text="Keep Texture")
        session_command(decimate, "DECIMATE", "Run MeshLab Decimation", "MOD_DECIM")

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        settings = context.blender_context.scene.remi_settings
        if action.id == "REMESH":
            from .service import create_candidate

            return stage_result(
                create_candidate(
                    context.source,
                    settings,
                    apply_result=True,
                    candidate=context.working_copy,
                    disk=context.disk,
                )
            )
        if action.id == "DECIMATE":
            from .decimation import create_candidate

            return stage_result(
                create_candidate(
                    context.working_copy,
                    settings,
                    disk=context.disk,
                )
            )
        return super().execute(action, context)
