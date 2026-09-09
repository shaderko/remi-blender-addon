"""Interactive and automatic retopology workflow feature."""

from __future__ import annotations

from ..base import FeatureDefaults, session_command, stage_result
from ...workflow.contracts import (
    ExecutionMode,
    FeatureAction,
    FeatureDescriptor,
    FeatureExecutionContext,
    StageResult,
)


class RetopologyFeature(FeatureDefaults):
    descriptor = FeatureDescriptor(
        id="RETOPOLOGY",
        name="Retopology",
        icon="MOD_TRIANGULATE",
        next_feature="UV",
        actions=(
            FeatureAction(
                "INSTANT_START",
                "Interactive Retopology",
                "Open the Instant Meshes retopology workspace",
                mode=ExecutionMode.INTERACTIVE,
            ),
            FeatureAction(
                "AUTO_RETOPO",
                "Auto Retopology",
                "Run the external AutoRemesher",
            ),
        ),
    )

    def draw(self, layout, context) -> None:
        blender_context = context.blender_context
        state = context.state
        layout.label(text="Retopology", icon="MOD_REMESH")
        instant_settings = blender_context.scene.remi_instant_meshes
        if state.interactive:
            from ... import instant_meshes

            instant_meshes.draw_panel(layout, blender_context, embedded=True)
            return

        row = layout.row(align=True)
        row.prop(instant_settings, "target_faces", text="Target")
        row.prop(instant_settings, "pure_quad", text="Pure Quads")
        row = layout.row(align=True)
        row.prop(instant_settings, "preserve_creases", text="Creases")
        if instant_settings.preserve_creases:
            row.prop(instant_settings, "crease_angle", text="Angle")
        layout.prop(instant_settings, "align_boundaries", text="Align Open Boundaries")
        layout.separator()
        action = layout.column()
        action.scale_y = 1.35
        session_command(
            action,
            "INSTANT_START",
            "Start Interactive Retopology",
            "PLAY",
        )

        layout.separator()
        automatic = layout.box()
        row = automatic.row()
        settings = blender_context.scene.remi_settings
        row.prop(settings, "use_autoremesher", text="")
        row.label(text="Automatic · External")
        if settings.use_autoremesher:
            automatic.prop(settings, "autoremesher_executable", text="Executable")
            row = automatic.row(align=True)
            row.prop(settings, "ar_target_quads", text="Target")
            row.prop(settings, "ar_adaptivity", text="Adaptive")
            row = automatic.row(align=True)
            row.prop(settings, "ar_edge_scaling", text="Edge Scale")
            row.prop(settings, "ar_sharp_edge", text="Sharp")
            automatic.prop(settings, "ar_smooth_normal", text="Smooth Normals")
            session_command(
                automatic,
                "AUTO_RETOPO",
                "Run AutoRemesher",
                "MOD_REMESH",
            )

    def execute(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> StageResult:
        if action.id == "AUTO_RETOPO":
            from ...operators import _create_autoremesher_candidate

            return stage_result(
                _create_autoremesher_candidate(
                    context.working_copy,
                    context.blender_context.scene.remi_settings,
                    disk=context.disk,
                )
            )
        return super().execute(action, context)

    def start_interactive(
        self,
        action: FeatureAction,
        context: FeatureExecutionContext,
    ) -> None:
        if action.id != "INSTANT_START":
            return super().start_interactive(action, context)
        from ...instant_meshes.runtime import runtime

        runtime.start(
            context.working_copy,
            context.blender_context.scene.remi_instant_meshes,
        )

    def cancel_interactive(self, action: FeatureAction) -> None:
        if action.id == "INSTANT_START":
            from ...instant_meshes.runtime import runtime

            runtime.shutdown()
