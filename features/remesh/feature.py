"""Remesh and reduction workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, session_command, stage_result
from ..contracts import RemeshUseCases
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
            FeatureAction("REMESH", "Remesh", "Create a clean watertight surface", automatic=True),
            FeatureAction("DECIMATE", "Decimate", "Reduce the mesh with MeshLab", automatic=True),
        ),
    )

    def __init__(self, service: RemeshUseCases):
        self._service = service

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
            return stage_result(
                self._service.remesh(
                    context.source,
                    settings,
                    candidate=context.working_copy,
                    disk=context.disk,
                )
            )
        if action.id == "DECIMATE":
            return stage_result(
                self._service.decimate(
                    context.working_copy,
                    settings,
                    disk=context.disk,
                )
            )
        return super().execute(action, context)

    def preflight_automatic(self, action, context):
        if action.id == "DECIMATE":
            from ...integrations import meshlab
            if not meshlab.ensure_pymeshlab():
                raise RuntimeError(meshlab.pymeshlab_unavailable_message())
        elif context.scene.remi_settings.use_hole_repair and context.scene.remi_settings.hole_repair_method == "ALPHA_WRAP":
            from ...integrations.alpha_wrap import toolchain
            error = toolchain.validate_executable(toolchain.resolve_executable(context.scene.remi_settings.alpha_wrap_executable))
            if error:
                raise RuntimeError(error + ". Build the helper in Repair before running this preset.")

    def draw_automatic_settings(self, layout, context, action):
        settings = context.scene.remi_settings
        if action.id == "DECIMATE":
            names = ("decimation_passes", "target_percentage", "decimation_preserve_detail", "decimation_with_texture")
        else:
            names = ("remesh_backend", "voxel_size", "use_sdf_fillet", "fillet_radius", "use_sdf_smoothing", "smoothing_iterations")
            if settings.remesh_backend == "VOLUME":
                names = ("remesh_backend", "voxel_size", "hole_close_ratio", "volume_guide_voxel_scale", "volume_surface_fit_ratio", "volume_preserve_features", "volume_feature_angle", "volume_feature_reach")
        for name in names:
            layout.prop(settings, name)
