"""Modal Blender adapters; the external worker never touches scene data."""
from pathlib import Path
import tempfile

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from ...blender.gaussian_splats import import_splats, splat_display_label, splat_for_image
from ...integrations.triposplat.client import GenerationJob
from .settings import default_python


class GenerationRuntime:
    job = None
    timer = None
    manager = None
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
                    self.manager.remi_generation.busy = False
                except (ReferenceError, AttributeError):
                    pass
            self.timer = self.manager = None


runtime = GenerationRuntime()


def _progress_label(phase):
    if phase.startswith("Loading"):
        return "Loading model."
    if phase.startswith("Using"):
        return "Generating."
    if phase.startswith("Removing"):
        return "Preparing image…"
    if phase.startswith(("Encoding", "Generating", "Decoding")):
        return "Generating…"
    if phase.startswith("Saving"):
        return "Saving…"
    return "Finishing…" if phase == "Complete" else "Starting…"


def _progress_fraction(phase):
    if phase.startswith("Loading"):
        return 0.08
    if phase.startswith("Removing"):
        return 0.16
    if phase.startswith("Encoding"):
        return 0.28
    if phase.startswith("Generating"):
        return 0.55
    if phase.startswith("Decoding"):
        return 0.82
    if phase.startswith("Saving"):
        return 0.94
    if phase == "Complete":
        return 1.0
    return 0.04


def can_generate(context):
    session = getattr(context.window_manager, "remi_session", None)
    generation = getattr(context.window_manager, "remi_generation", None)
    texture = getattr(context.window_manager, "remi_texture", None)
    images = getattr(context.window_manager, "remi_image", None)
    return bool(
        context.mode == "OBJECT"
        and not (session and session.active)
        and not (generation and generation.mesh_busy)
        and not (texture and texture.busy)
        and not (images and images.busy)
        and runtime.job is None
    )


class Remi_OT_GenerateSplats(Operator):
    bl_idname = "remi.generate_splats"
    bl_label = "Generate from Image"
    bl_description = "Generate native Gaussian splats locally with rand6_100_ncm"
    bl_options = {"REGISTER", "UNDO"}

    image_path: StringProperty(
        name="Image", subtype="FILE_PATH",
        description="Generate 3D from this image instead of the manual Source Image field",
    )

    @classmethod
    def poll(cls, context):
        return can_generate(context)

    def invoke(self, context, event):
        settings = context.scene.remi_generation
        state = context.window_manager.remi_generation
        image = bpy.path.abspath(self.image_path or settings.image)
        if context.window is None:
            self.report({"ERROR"}, "Generation needs an interactive Blender window")
            return {"CANCELLED"}
        try:
            if not image or not Path(image).is_file():
                raise ValueError("Choose an image first")
            if not settings.output or not settings.models:
                raise ValueError("Choose a model folder and output folder in Create settings")
            runtime.job = GenerationJob.start(
                python=bpy.path.abspath(settings.python) if settings.python else default_python(),
                models=bpy.path.abspath(settings.models),
                image=image, output=bpy.path.abspath(settings.output),
                gaussians=int(settings.gaussians), seed=settings.seed,
                cache_minutes=int(settings.model_cache_minutes),
                cache_dir=Path(bpy.app.tempdir or tempfile.gettempdir()) / "remi-generation-cache",
            )
            runtime.manager = context.window_manager
            runtime.timer = runtime.manager.event_timer_add(0.25, window=context.window)
            runtime.cancel_requested = False
            state.busy = True
            state.status = "Starting…"
            state.status_kind = "PROGRESS"
            state.progress = 0.02
            state.source_image = image
            state.mesh_status = ""
            state.mesh_status_kind = "IDLE"
            state.mesh_source_object = ""
            state.directory = str(runtime.job.directory)
            settings.image = image
            image_state = getattr(context.window_manager, "remi_image", None)
            if image_state is not None:
                image_state.selected = image
            self._scene = context.scene
            self._preview_limit = settings.preview_limit
            context.window_manager.modal_handler_add(self)
        except Exception as exc:
            runtime.stop()
            state.busy = False
            state.progress = 0.0
            state.status = str(exc)
            state.status_kind = "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        state = context.window_manager.remi_generation
        if runtime.job is None:
            return {"CANCELLED"}
        if runtime.cancel_requested or (event.type == "ESC" and event.value == "PRESS"):
            runtime.stop()
            state.busy = False
            state.progress = 0.0
            state.status = "Cancelled. Logs and any completed files are kept in Output Folder."
            state.status_kind = "CANCELLED"
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        phase = runtime.job.status().get("phase", "Generating")
        state.status = _progress_label(phase)
        state.progress = _progress_fraction(phase)
        if context.area:
            context.area.tag_redraw()
        if runtime.job.poll() is None:
            return {"RUNNING_MODAL"}
        try:
            path = runtime.job.result()
            session = getattr(context.window_manager, "remi_session", None)
            if context.scene != self._scene or context.mode != "OBJECT" or (session and session.active):
                raise RuntimeError("Generation saved, but scene/mode changed. Import splats.npz when ready")
            obj = import_splats(context, path, preview_limit=self._preview_limit)
            image = bpy.path.abspath(self.image_path or context.scene.remi_generation.image)
            if image:
                obj["remi_source_image"] = str(Path(image).resolve())
            state.mesh_source_object = ""
            state.progress = 1.0
            state.status = f"Generated {obj['remi_splat_count']:,} Gaussians · {splat_display_label(obj)}"
            state.status_kind = "SUCCESS"
            self.report({"INFO"}, state.status)
            return {"FINISHED"}
        except Exception as exc:
            state.progress = 0.0
            state.status = str(exc)
            state.status_kind = "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            runtime.stop()

    def cancel(self, context):
        runtime.stop()


class Remi_OT_ActivateSplat(Operator):
    bl_idname = "remi.activate_splat"
    bl_label = "Select 3D Result"
    bl_description = "Select the splat object generated from this image"

    image_path: StringProperty(name="Image", subtype="FILE_PATH")

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        splat = splat_for_image(self.image_path)
        if splat is None:
            self.report({"ERROR"}, "No 3D result is linked to this image")
            return {"CANCELLED"}
        for obj in context.selected_objects:
            obj.select_set(False)
        splat.hide_set(False)
        splat.select_set(True)
        context.view_layer.objects.active = splat
        return {"FINISHED"}


class Remi_OT_CancelGeneration(Operator):
    bl_idname = "remi.cancel_generation"
    bl_label = "Cancel Generation"
    bl_description = "Stop generation or model download, keeping completed files"

    @classmethod
    def poll(cls, context):
        return runtime.job is not None

    def execute(self, context):
        runtime.cancel_requested = True
        return {"FINISHED"}


class Remi_OT_ImportSplats(Operator, ImportHelper):
    bl_idname = "remi.import_splats"
    bl_label = "Import Generated Splats"
    bl_description = "Import a previously generated splats.npz model"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".npz"
    filter_glob: StringProperty(default="*.npz", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return can_generate(context)

    def execute(self, context):
        state = context.window_manager.remi_generation
        try:
            obj = import_splats(context, bpy.path.abspath(self.filepath),
                                preview_limit=context.scene.remi_generation.preview_limit)
            state.status = f"Imported {obj['remi_splat_count']:,} Gaussians"
            state.status_kind = "SUCCESS"
            state.mesh_status = ""
            state.mesh_status_kind = "IDLE"
            state.mesh_source_object = ""
            state.directory = str(Path(bpy.path.abspath(self.filepath)).resolve().parent)
            self.report({"INFO"}, f"Imported {obj['remi_splat_count']:,} native Gaussians")
        except Exception as exc:
            state.status = str(exc)
            state.status_kind = "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}
