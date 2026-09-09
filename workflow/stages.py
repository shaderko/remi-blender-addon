"""Mesh-processing stages executed only through a Remi session transaction."""

from __future__ import annotations


class WorkflowStage:
    command = ""
    label = ""
    next_stage = ""
    requires_source_checkpoint = False
    interactive = False

    def queued_message(self, context, source):
        return f"Preparing {self.label.lower()}…"

    def build(self, context, source, working_copy, source_checkpoint=None, disk=None):
        raise NotImplementedError


class Repair(WorkflowStage):
    command = "REPAIR"
    label = "Repair"
    next_stage = "REMESH"

    def build(self, context, source, working_copy, source_checkpoint=None, disk=None):
        from ..operators import _create_repair_candidate

        return _create_repair_candidate(
            source,
            context.scene.remi_settings,
            candidate=working_copy,
            disk=disk,
        )


class ManualRepair(WorkflowStage):
    command = "MANUAL_REPAIR"
    label = "Manual Repair"
    next_stage = "REPAIR"

    def __init__(self, ring_world, ring_normals=None):
        self.ring_world = ring_world
        self.ring_normals = ring_normals

    def build(self, context, source, working_copy, source_checkpoint=None, disk=None):
        from ..operators import _create_surface_ring_patch

        return _create_surface_ring_patch(
            source,
            context.scene.remi_settings,
            self.ring_world,
            ring_normals=self.ring_normals,
            result=working_copy,
        )


class Remesh(WorkflowStage):
    command = "REMESH"
    label = "Remesh"
    next_stage = "RETOPOLOGY"

    def build(self, context, source, working_copy, source_checkpoint=None, disk=None):
        from ..operators import _create_sdf_candidate

        return _create_sdf_candidate(
            source,
            context.scene.remi_settings,
            apply_result=True,
            candidate=working_copy,
            disk=disk,
        )


class Decimate(WorkflowStage):
    command = "DECIMATE"
    label = "Decimate"
    next_stage = "RETOPOLOGY"

    def build(self, context, source, working_copy, source_checkpoint=None, disk=None):
        from ..operators import _create_decimate_candidate

        return _create_decimate_candidate(
            working_copy,
            context.scene.remi_settings,
            disk=disk,
        )


class Retopology(WorkflowStage):
    command = "INSTANT_START"
    label = "Retopology"
    next_stage = "UV"
    interactive = True

    def start(self, context, current):
        from ..instant_meshes.runtime import runtime as instant_runtime

        instant_runtime.start(current, context.scene.remi_instant_meshes)

    @staticmethod
    def cancel():
        from ..instant_meshes.runtime import runtime as instant_runtime

        instant_runtime.shutdown()


class AutoRetopology(WorkflowStage):
    command = "AUTO_RETOPO"
    label = "Auto Retopology"
    next_stage = "UV"

    def build(self, context, source, working_copy, source_checkpoint=None, disk=None):
        from ..operators import _create_autoremesher_candidate

        return _create_autoremesher_candidate(
            working_copy,
            context.scene.remi_settings,
            disk=disk,
        )


class UV(WorkflowStage):
    command = "UV"
    label = "UV"
    next_stage = "BAKE"

    def queued_message(self, context, source):
        return (
            f"Generating UVs for {len(source.data.polygons):,} faces… "
            "Blender stays busy while the atlas is built."
        )

    def build(self, context, source, working_copy, source_checkpoint=None, disk=None):
        from ..operators import _create_uv_candidate

        return _create_uv_candidate(
            source,
            context.scene.remi_settings,
            candidate=working_copy,
        )


class Bake(WorkflowStage):
    requires_source_checkpoint = True
    next_stage = "BAKE"

    def __init__(self, command, label, passes):
        self.command = command
        self.label = label
        self.passes = passes

    def queued_message(self, context, source):
        size = context.scene.remi_settings.bake_texture_size
        return (
            f"{self.label} at {size}×{size}… "
            "Blender stays busy while Cycles bakes."
        )

    def build(self, context, source, working_copy, source_checkpoint=None, disk=None):
        from ..operators import _create_bake_candidate

        return _create_bake_candidate(
            source_checkpoint,
            source,
            context.scene.remi_settings,
            passes=self.passes,
            name_prefix=source.name,
            candidate=working_copy,
        )


_STAGES = {
    stage.command: stage
    for stage in (
        Repair(),
        Remesh(),
        Decimate(),
        Retopology(),
        AutoRetopology(),
        UV(),
        Bake("BAKE_ALL", "Bake All Maps", ("diffuse", "roughness", "normal", "ao")),
        Bake("BAKE_DIFFUSE", "Bake Albedo", ("diffuse",)),
        Bake("BAKE_ROUGHNESS", "Bake Roughness", ("roughness",)),
        Bake("BAKE_NORMAL", "Bake Normal", ("normal",)),
        Bake("BAKE_AO", "Bake AO", ("ao",)),
    )
}


def stage_for_command(command: str):
    return _STAGES.get(command)
