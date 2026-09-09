"""Repair workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, session_command, stage_result
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

    def draw(self, layout, context) -> None:
        settings = context.blender_context.scene.remi_settings
        state = context.state
        layout.label(text="Repair", icon="MOD_REMESH")

        if state.interactive:
            notice = layout.box()
            notice.label(text="Manual repair active", icon="BRUSH_DATA")
            notice.label(text="Draw on the surface around one hole")
            notice.label(text="Release to apply · Esc or right-click to cancel")
            return

        manual = layout.box()
        manual.label(text="Manual · One Hole", icon="BRUSH_DATA")
        manual.label(text="Draw on the intact surface around the rim")
        row = manual.row(align=True)
        row.prop(settings, "targeted_ray_spacing", text="Ray px")
        row.prop(settings, "targeted_ray_depth_ratio", text="Depth")
        row = manual.row(align=True)
        row.prop(settings, "alpha_wrap_patch_resolution", text="Resolution")
        row.prop(settings, "alpha_wrap_patch_relax_iterations", text="Relax")
        action = manual.column()
        action.scale_y = 1.35
        action.operator("remi.draw_hole_patch", text="Draw Around Hole", icon="BRUSH_DATA")

        layout.separator()
        layout.label(text="Automatic Repair")
        layout.prop(settings, "hole_repair_method", text="Method")

        if settings.hole_repair_method == "ALPHA_WRAP":
            layout.prop(settings, "alpha_wrap_alpha_ratio", text="Hole Scale")
            layout.prop(settings, "alpha_wrap_auto_scale", text="Find Scale Automatically")
            if settings.alpha_wrap_auto_scale:
                row = layout.row(align=True)
                row.prop(settings, "alpha_wrap_max_ratio", text="Maximum")
                row.prop(settings, "alpha_wrap_coverage_target", text="Coverage")
            row = layout.row(align=True)
            row.prop(settings, "alpha_wrap_patch_ratio", text="Detection")
            row.prop(settings, "alpha_wrap_patch_rings", text="Overlap")
            layout.prop(settings, "alpha_wrap_offset_ratio", text="Surface Offset")
            layout.prop(settings, "alpha_wrap_executable", text="Helper")
            layout.prop(settings, "alpha_wrap_auto_build", text="Build Automatically")
            layout.operator("remi.build_alpha_wrap", text="Build Helper", icon="TOOL_SETTINGS")
        elif settings.hole_repair_method in {"HYBRID", "BOUNDARY"}:
            row = layout.row(align=True)
            row.prop(settings, "hole_max_sides", text="Max Loop")
            row.prop(settings, "hole_weld_distance", text="Weld")
        elif settings.hole_repair_method == "VOLUME":
            layout.prop(settings, "hole_close_ratio", text="Crack Size")
            layout.prop(settings, "volume_guide_voxel_scale", text="Resolution")
            layout.prop(settings, "volume_surface_fit_ratio", text="Surface Fit Reach")
            row = layout.row(align=True)
            row.prop(settings, "alpha_wrap_patch_ratio", text="Detection")
            row.prop(settings, "alpha_wrap_patch_rings", text="Overlap")

        if settings.hole_repair_method in {"HYBRID", "VOLUME"}:
            layout.prop(settings, "hole_detail_recovery", text="Recover Surface Detail")
            if settings.hole_detail_recovery and settings.hole_repair_method == "HYBRID":
                layout.prop(settings, "hole_detail_ratio", text="Detail Reach")

        layout.separator()
        action = layout.column()
        action.scale_y = 1.35
        session_command(action, "REPAIR", "Run Repair", "MOD_REMESH")

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        settings = context.blender_context.scene.remi_settings
        if action.id == "REPAIR":
            from .service import _create_repair_candidate

            return stage_result(
                _create_repair_candidate(
                    context.source,
                    settings,
                    candidate=context.working_copy,
                    disk=context.disk,
                )
            )
        if action.id == "MANUAL_REPAIR":
            from .service import _create_surface_ring_patch

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
