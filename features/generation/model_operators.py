"""Explicit, cancellable model installation, outside the Blender extension folder."""
import bpy
from bpy.types import Operator

from ...integrations.triposplat.client import ModelDownloadJob, shutdown_generation_server
from ...integrations.triposplat.download import model_base
from .operators import can_generate, runtime
from .settings import default_python


class Remi_OT_DownloadModels(Operator):
    bl_idname = "remi.download_models"
    bl_label = "Download Models"
    bl_description = "Download and verify local generation weights from public model storage (about 4.5 GB)"

    @classmethod
    def poll(cls, context):
        return can_generate(context)

    def invoke(self, context, event):
        settings = context.scene.remi_generation
        try:
            self._check_access()
            model_base(settings.download_url)
            if not settings.models:
                raise ValueError("Choose a Model Folder first")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=480)

    @staticmethod
    def _check_access():
        if not bpy.app.online_access:
            raise ValueError("Enable Allow Online Access in Blender Preferences to download models")

    def draw(self, context):
        settings = context.scene.remi_generation
        self.layout.label(text="Download generation weights (about 4.5 GB)?", icon="IMPORT")
        self.layout.label(text="Verified existing files are reused. Your images are not uploaded.")
        self.layout.label(text=settings.download_url)
        self.layout.label(text=bpy.path.abspath(settings.models))

    def execute(self, context):
        settings = context.scene.remi_generation
        state = context.window_manager.remi_generation
        try:
            self._check_access()
            if context.window is None or not settings.models:
                raise ValueError("Choose a Model Folder in an interactive Blender window")
            shutdown_generation_server()
            runtime.job = ModelDownloadJob.start(
                python=bpy.path.abspath(settings.python) if settings.python else default_python(),
                models=bpy.path.abspath(settings.models), base_url=settings.download_url,
            )
            runtime.manager = context.window_manager
            runtime.timer = runtime.manager.event_timer_add(0.25, window=context.window)
            runtime.cancel_requested = False
            state.busy = True
            state.status = "Reading model manifest…"
            state.status_kind = "PROGRESS"
            state.directory = str(runtime.job.directory)
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except Exception as exc:
            runtime.stop()
            state.status, state.status_kind = str(exc), "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def modal(self, context, event):
        state = context.window_manager.remi_generation
        if runtime.job is None:
            return {"CANCELLED"}
        if runtime.cancel_requested or (event.type == "ESC" and event.value == "PRESS"):
            runtime.stop()
            state.status = "Download cancelled. Verified model files are kept; retry to finish."
            state.status_kind = "CANCELLED"
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        state.status = runtime.job.status().get("phase", "Downloading models…")
        if context.area:
            context.area.tag_redraw()
        if runtime.job.poll() is None:
            return {"RUNNING_MODAL"}
        try:
            runtime.job.result()
            state.status, state.status_kind = "Models downloaded and verified", "SUCCESS"
            self.report({"INFO"}, state.status)
            return {"FINISHED"}
        except Exception as exc:
            state.status, state.status_kind = str(exc), "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            runtime.stop()

    def cancel(self, context):
        runtime.stop()
