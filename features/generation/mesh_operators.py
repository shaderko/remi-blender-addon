"""Modal Blender adapter for the external Gaussian-to-mesh worker."""
from pathlib import Path
import tempfile
import uuid

import bpy
from bpy.types import Operator

from ...blender.gaussian_splats import is_splat_object, native_splats_available, write_splat_archive
from ...blender.reconstructed_mesh import import_reconstructed_mesh
from ...integrations.splat_mesh.client import MeshJob
from ...integrations.triposplat.client import shutdown_generation_server


class MeshRuntime:
    job = None
    timer = None
    manager = None
    temp_archive = None
    cancel_requested = False

    def stop(self):
        try:
            if self.job is not None:
                self.job.cancel()
        finally:
            self.job = None
            self.cancel_requested = False
            if self.timer is not None and self.manager is not None:
                try:
                    self.manager.event_timer_remove(self.timer)
                except (ReferenceError, RuntimeError):
                    pass
            if self.manager is not None:
                try:
                    self.manager.remi_generation.mesh_busy = False
                except (ReferenceError, AttributeError):
                    pass
            if self.temp_archive is not None:
                try:
                    Path(self.temp_archive).unlink(missing_ok=True)
                except OSError:
                    pass
            self.temp_archive = None
            self.timer = self.manager = None


runtime = MeshRuntime()


def splat_source(context):
    """Only the active splat is a valid source; never reuse a global archive."""
    obj = context.view_layer.objects.active
    return obj if is_splat_object(obj) else None


def can_convert(context):
    session = getattr(context.window_manager, "remi_session", None)
    generation = getattr(context.window_manager, "remi_generation", None)
    return bool(
        context.mode == "OBJECT"
        and not (session and session.active)
        and not (generation and generation.busy)
        and not (generation and generation.mesh_busy)
        and runtime.job is None
        and splat_source(context) is not None
        and native_splats_available()
    )


def _phase_label(phase):
    return "Finishing…" if phase == "Complete" else phase


class Remi_OT_CreateMesh(Operator):
    bl_idname = "remi.create_mesh"
    bl_label = "Create Mesh"
    bl_description = "Create a camera-aware surface constrained by rendered Gaussian silhouettes; preserve dense detail"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return can_convert(context)

    def invoke(self, context, event):
        settings = context.scene.remi_generation
        state = context.window_manager.remi_generation
        if context.window is None:
            self.report({"ERROR"}, "Mesh conversion needs an interactive Blender window")
            return {"CANCELLED"}
        source = splat_source(context)
        try:
            if source is None:
                raise ValueError("Select a Remi Gaussian splat object first")
            if not settings.python:
                raise ValueError("Choose the external Python in generation settings")
            if not settings.mesh_output:
                raise ValueError("Choose a Mesh Output folder in generation settings")
            temporary = Path(bpy.app.tempdir or tempfile.gettempdir())
            temporary = temporary / f"remi_mesh_source_{uuid.uuid4().hex}.npz"
            runtime.temp_archive = temporary
            write_splat_archive(source, temporary)
            shutdown_generation_server()  # Release this add-on's warm model before GPU reconstruction.
            runtime.job = MeshJob.start(
                python=bpy.path.abspath(settings.python),
                archive=temporary,
                output=bpy.path.abspath(settings.mesh_output),
                quality=settings.mesh_quality,
                source=source.name,
            )
            runtime.manager = context.window_manager
            runtime.timer = runtime.manager.event_timer_add(0.25, window=context.window)
            runtime.cancel_requested = False
            state.mesh_busy = True
            state.mesh_status = "Starting mesher…"
            state.mesh_status_kind = "PROGRESS"
            state.mesh_source_object = source.name
            state.mesh_directory = str(runtime.job.directory)
            self._source_name = source.name
            self._source_object = source
            self._source_matrix = source.matrix_world.copy()
            self._scene = context.scene
            context.window_manager.modal_handler_add(self)
        except Exception as exc:
            runtime.stop()
            state.mesh_status = str(exc)
            state.mesh_status_kind = "ERROR"
            state.mesh_source_object = source.name if source is not None else ""
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        state = context.window_manager.remi_generation
        if runtime.job is None:
            return {"CANCELLED"}
        if runtime.cancel_requested or (event.type == "ESC" and event.value == "PRESS"):
            runtime.stop()
            state.mesh_status = "Cancelled. Partial output is kept in Mesh Output."
            state.mesh_status_kind = "CANCELLED"
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        state.mesh_status = _phase_label(runtime.job.status().get("phase", "Building surface"))
        if context.area:
            context.area.tag_redraw()
        if runtime.job.poll() is None:
            return {"RUNNING_MODAL"}
        try:
            path = runtime.job.result()
            if context.scene != self._scene or context.mode != "OBJECT":
                raise RuntimeError("Mesh saved, but the scene or mode changed. Import mesh.ply when ready")
            try:
                source = self._source_object
                if not is_splat_object(source):
                    source = None
            except ReferenceError:
                source = None
            obj = import_reconstructed_mesh(
                context, path, source_object=source,
                report=runtime.job.directory / "report.json",
                source_matrix=self._source_matrix,
            )
            state.mesh_source_object = source.name if source is not None else self._source_name
            state.mesh_status = f"Mesh ready · {len(obj.data.polygons):,} triangles"
            state.mesh_status_kind = "SUCCESS"
            self._scene.remi_flow.preset = "RECONSTRUCTED"
            context.window_manager.remi_ui.workspace = "OPTIMIZE"
            self.report({"INFO"}, state.mesh_status)
            return {"FINISHED"}
        except Exception as exc:
            state.mesh_status = str(exc)
            state.mesh_status_kind = "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            runtime.stop()

    def cancel(self, context):
        runtime.stop()


class Remi_OT_CancelMesh(Operator):
    bl_idname = "remi.cancel_mesh"
    bl_label = "Cancel Mesh"
    bl_description = "Stop Gaussian-to-mesh conversion without deleting outputs"

    @classmethod
    def poll(cls, context):
        return runtime.job is not None

    def execute(self, context):
        runtime.cancel_requested = True
        return {"FINISHED"}
