"""Blender adapters for local Iris image generation and the image gallery."""

from datetime import datetime
import json
from pathlib import Path
import shutil
import uuid

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from ...integrations.iris3b import IrisDownloadJob, IrisJob
from ...integrations.iris3b.constants import MODEL_BYTES, MODEL_LABELS, SIZE_BUCKETS, TEXT_ENCODER_BYTES
from .gallery import gallery
from .settings import dimensions


class ImageRuntime:
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
                    self.manager.remi_image.busy = False
                except (ReferenceError, AttributeError):
                    pass
            self.timer = self.manager = None


runtime = ImageRuntime()


def _generation_settings(context):
    return context.scene.remi_generation


def _python(context):
    from ..generation.settings import default_python

    value = _generation_settings(context).python
    return bpy.path.abspath(value) if value else default_python()


def _image_progress(phase):
    if phase.startswith("Loading"):
        return 0.08
    if phase.startswith(("Encoding", "Preparing")):
        return 0.2
    if phase.startswith("Generating image"):
        try:
            index, total = phase.rsplit(" ", 1)[-1].split("/")
            return 0.28 + 0.64 * (int(index) - 1) / max(1, int(total))
        except (ValueError, TypeError):
            return 0.5
    if phase == "Complete":
        return 1.0
    return 0.04


def can_generate(context):
    session = getattr(context.window_manager, "remi_session", None)
    generation = getattr(context.window_manager, "remi_generation", None)
    texture = getattr(context.window_manager, "remi_texture", None)
    images = getattr(context.window_manager, "remi_image", None)
    return bool(
        not (session and session.active)
        and not (generation and (generation.busy or generation.mesh_busy))
        and not (texture and texture.busy)
        and not (images and images.busy)
        and runtime.job is None
    )


class _JobOperator:
    label = "Iris"

    def _start_modal(self, context, job, status):
        state = context.window_manager.remi_image
        runtime.job = job
        runtime.manager = context.window_manager
        runtime.timer = runtime.manager.event_timer_add(0.25, window=context.window)
        runtime.cancel_requested = False
        state.busy = True
        state.status = status
        state.status_kind = "PROGRESS"
        state.progress = 0.02
        state.directory = str(job.directory)
        context.window_manager.modal_handler_add(self)

    def modal(self, context, event):
        state = context.window_manager.remi_image
        if runtime.job is None:
            return {"CANCELLED"}
        if runtime.cancel_requested or (event.type == "ESC" and event.value == "PRESS"):
            runtime.stop()
            state.progress = 0.0
            state.status = f"{self.label} cancelled. Completed files are kept."
            state.status_kind = "CANCELLED"
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        status = runtime.job.status()
        phase = status.get("phase", self.label)
        state.status = phase
        if "percent" in status:
            state.progress = max(0.0, min(1.0, float(status["percent"]) / 100.0))
        else:
            state.progress = _image_progress(phase)
        if context.area:
            context.area.tag_redraw()
        if runtime.job.poll() is None:
            return {"RUNNING_MODAL"}
        try:
            result = self._finish(runtime, context)
            state.status = result
            state.status_kind = "SUCCESS"
            state.progress = 1.0
            self.report({"INFO"}, result)
            return {"FINISHED"}
        except Exception as exc:
            state.status = str(exc)
            state.status_kind = "ERROR"
            state.progress = 0.0
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            runtime.stop()

    def cancel(self, context):
        runtime.stop()


class Remi_OT_GenerateImage(_JobOperator, Operator):
    bl_idname = "remi.generate_image"
    bl_label = "Generate Image"
    bl_description = "Generate a concept image locally with Iris-3B"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        settings = context.scene.remi_image
        return can_generate(context) and bool(settings.prompt.strip())

    def invoke(self, context, event):
        settings = context.scene.remi_image
        state = context.window_manager.remi_image
        if context.window is None:
            self.report({"ERROR"}, "Image generation needs an interactive Blender window")
            return {"CANCELLED"}
        try:
            width, height = dimensions(settings)
            job = IrisJob.start(
                python=_python(context),
                models=bpy.path.abspath(settings.models),
                output=bpy.path.abspath(settings.output),
                variant=settings.model,
                prompt=settings.prompt,
                negative_prompt=settings.negative_prompt if settings.model == "base" else "",
                width=width,
                height=height,
                steps=settings.steps,
                cfg_scale=float(settings.cfg_scale),
                seed=settings.seed,
                count=settings.count,
                memory_mode=settings.memory_mode,
                cache_minutes=settings.cache_minutes,
            )
            self._start_modal(context, job, "Starting Iris worker…")
        except Exception as exc:
            runtime.stop()
            state.status, state.status_kind = str(exc), "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _finish(self, runtime, context):
        directory = runtime.job.result()
        state = context.window_manager.remi_image
        meta = {}
        try:
            import json

            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        images = meta.get("images") or []
        first = directory / (images[0]["file"] if images else "image_000.png")
        state.directory = str(directory)
        state.selected = str(first) if first.is_file() else ""
        state.page = 0
        gallery.invalidate()
        count = len(images) or 1
        return f"Generated {count} image{'s' if count != 1 else ''} · {MODEL_LABELS.get(meta.get('model'), 'Iris-3B')}"


class Remi_OT_CancelImage(Operator):
    bl_idname = "remi.cancel_image"
    bl_label = "Cancel Image Generation"
    bl_description = "Stop the local Iris worker, keeping completed files"

    @classmethod
    def poll(cls, context):
        return runtime.job is not None

    def execute(self, context):
        runtime.cancel_requested = True
        return {"FINISHED"}


class Remi_OT_DownloadIris(_JobOperator, Operator):
    bl_idname = "remi.download_iris"
    bl_label = "Download Image Model"
    bl_description = "Download and verify Iris-3B and its Qwen3-VL text encoder"
    label = "Model download"

    @classmethod
    def poll(cls, context):
        return can_generate(context)

    @staticmethod
    def _check_access():
        if not bpy.app.online_access:
            raise ValueError("Enable Allow Online Access in Blender Preferences to download models")

    def invoke(self, context, event):
        try:
            self._check_access()
            if not _python(context):
                raise ValueError("Choose an external CUDA/PyTorch Python in Images settings")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        settings = context.scene.remi_image
        variant = settings.model
        size = MODEL_BYTES[variant] / 1024**3
        encoder = TEXT_ENCODER_BYTES / 1024**3
        self.layout.label(text=f"Download {MODEL_LABELS[variant]} (about {size:.1f} GB)?", icon="IMPORT")
        self.layout.label(text=f"Qwen3-VL text encoder: about {encoder:.1f} GB, downloaded once")
        self.layout.label(text="Weights are Apache-2.0 and downloaded from Hugging Face.")
        self.layout.label(text=bpy.path.abspath(settings.models))

    def execute(self, context):
        settings = context.scene.remi_image
        state = context.window_manager.remi_image
        try:
            self._check_access()
            if context.window is None:
                raise ValueError("Model download needs an interactive Blender window")
            job = IrisDownloadJob.start(
                python=_python(context),
                models=bpy.path.abspath(settings.models),
                variant=settings.model,
                text_encoder=True,
            )
            self._start_modal(context, job, "Preparing model download…")
        except Exception as exc:
            runtime.stop()
            state.status, state.status_kind = str(exc), "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _finish(self, runtime, context):
        runtime.job.result()
        gallery.invalidate()
        return f"{MODEL_LABELS.get(context.scene.remi_image.model, 'Iris-3B')} and text encoder ready"


class Remi_OT_AddImage(Operator, ImportHelper):
    bl_idname = "remi.add_image"
    bl_label = "Add Image"
    bl_description = "Add one or more images as 3D source thumbnails"
    filter_glob: StringProperty(
        default="*.png;*.jpg;*.jpeg;*.webp;*.tif;*.tiff;*.bmp",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return can_generate(context)

    def execute(self, context):
        settings = context.scene.remi_image
        state = context.window_manager.remi_image
        output = Path(bpy.path.abspath(settings.output))
        sources = []
        for item in getattr(self, "files", ()):
            path = Path(item.path)
            if path.is_file():
                sources.append(path)
            elif getattr(item, "name", ""):
                sources.append(path / item.name)
        if not sources and self.filepath:
            sources = [Path(bpy.path.abspath(self.filepath))]
        added = []
        try:
            output.mkdir(parents=True, exist_ok=True)
            for source in sources:
                if not source.is_file():
                    continue
                job = output / (
                    datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
                )
                job.mkdir(parents=True, exist_ok=False)
                destination = job / ("image_000" + (source.suffix.lower() or ".png"))
                shutil.copy2(source, destination)
                width = height = 0
                try:
                    image = bpy.data.images.load(str(destination), check_existing=False)
                    width, height = image.size
                    bpy.data.images.remove(image)
                except Exception:
                    pass
                metadata = {
                    "schema": 1,
                    "model": "upload",
                    "model_label": "Uploaded Image",
                    "prompt": source.stem.replace("_", " ").replace("-", " ").strip(),
                    "negative_prompt": "",
                    "width": int(width),
                    "height": int(height),
                    "steps": 0,
                    "cfg_scale": 0.0,
                    "seed": 0,
                    "count": 1,
                    "images": [{"file": destination.name, "seed": 0}],
                    "source": "upload",
                }
                (job / "meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                (job / "complete.json").write_text(
                    '{"schema": 1, "complete": true}', encoding="utf-8"
                )
                added.append(destination)
        except Exception as exc:
            state.status, state.status_kind = str(exc), "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not added:
            state.status, state.status_kind = "No images were added", "ERROR"
            self.report({"ERROR"}, state.status)
            return {"CANCELLED"}
        state.selected = str(added[0])
        state.refresh_token += 1
        gallery.invalidate()
        state.status = f"Added {len(added)} image{'s' if len(added) != 1 else ''} for 3D"
        state.status_kind = "SUCCESS"
        return {"FINISHED"}


class Remi_OT_SelectImage(Operator):
    bl_idname = "remi.select_image"
    bl_label = "Select Generated Image"
    bl_description = "Show this image's details and actions"

    path: StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        context.window_manager.remi_image.selected = bpy.path.abspath(self.path)
        return {"FINISHED"}


class Remi_OT_UseImageFor3D(Operator):
    bl_idname = "remi.use_image_for_3d"
    bl_label = "Use for 3D"
    bl_description = "Set this image as the input for 3D generation on the board"

    path: StringProperty(subtype="FILE_PATH")

    @classmethod
    def poll(cls, context):
        return bpy.ops.remi.select_workspace.poll()

    def execute(self, context):
        path = bpy.path.abspath(self.path)
        if not Path(path).is_file():
            self.report({"ERROR"}, f"Image not found: {path}")
            return {"CANCELLED"}
        context.scene.remi_generation.image = path
        self.report({"INFO"}, "Image selected. Use Generate 3D on its Remi Board card.")
        return {"FINISHED"}


class Remi_OT_OpenImage(Operator):
    bl_idname = "remi.open_image"
    bl_label = "Open Image"
    bl_description = "Open the generated image on this computer"

    path: StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        path = bpy.path.abspath(self.path)
        if not Path(path).is_file():
            self.report({"ERROR"}, f"Image not found: {path}")
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=path)
        return {"FINISHED"}


class Remi_OT_DeleteImage(Operator):
    bl_idname = "remi.delete_image"
    bl_label = "Delete Generated Image"
    bl_description = "Remove this generated image from disk"

    path: StringProperty(subtype="FILE_PATH")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        path = bpy.path.abspath(self.path)
        entry = next((item for item in gallery.refresh(context.scene.remi_image.output, context.window_manager.remi_image.refresh_token, force=True) if str(item.path) == path), None)
        if entry is None:
            self.report({"ERROR"}, "Image is no longer in the gallery")
            return {"CANCELLED"}
        if not gallery.remove(entry):
            self.report({"ERROR"}, f"Could not delete {path}")
            return {"CANCELLED"}
        state = context.window_manager.remi_image
        if bpy.path.abspath(state.selected) == path:
            state.selected = ""
        state.refresh_token += 1
        return {"FINISHED"}


class Remi_OT_RegenerateImage(Operator):
    bl_idname = "remi.regenerate_image"
    bl_label = "Load Settings"
    bl_description = "Load this image's prompt, model, size, and seed for another generation"

    path: StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        import json

        path = Path(bpy.path.abspath(self.path))
        try:
            meta = json.loads((path.parent / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"Metadata unavailable: {exc}")
            return {"CANCELLED"}
        settings = context.scene.remi_image
        settings.prompt = str(meta.get("prompt", ""))
        settings.negative_prompt = str(meta.get("negative_prompt", ""))
        if meta.get("model") in {"base", "turbo"}:
            settings.model = meta["model"]
        width, height = int(meta.get("width", 0)), int(meta.get("height", 0))
        for key, value in SIZE_BUCKETS.items():
            if value == (width, height):
                settings.size = key
                break
        seed = int(meta.get("seed", 0))
        for item in meta.get("images", []):
            if isinstance(item, dict) and item.get("file") == path.name:
                seed = int(item.get("seed", seed))
                break
        settings.seed = seed
        if settings.model == "base":
            settings.steps = int(meta.get("steps", settings.steps))
            settings.cfg_scale = int(round(float(meta.get("cfg_scale", settings.cfg_scale))))
        self.report({"INFO"}, "Settings loaded; review the prompt and generate")
        return {"FINISHED"}


class Remi_OT_CopyPrompt(Operator):
    bl_idname = "remi.copy_image_prompt"
    bl_label = "Copy Prompt"
    bl_description = "Copy this image prompt to the clipboard"

    path: StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        import json

        path = Path(bpy.path.abspath(self.path))
        try:
            meta = json.loads((path.parent / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"Metadata unavailable: {exc}")
            return {"CANCELLED"}
        context.window_manager.clipboard = str(meta.get("prompt", ""))
        return {"FINISHED"}


class Remi_OT_RefreshImages(Operator):
    bl_idname = "remi.refresh_images"
    bl_label = "Refresh Images"
    bl_description = "Rescan the output folder for generated images"

    def execute(self, context):
        gallery.invalidate()
        context.window_manager.remi_image.refresh_token += 1
        return {"FINISHED"}


class Remi_OT_ImagePage(Operator):
    bl_idname = "remi.image_page"
    bl_label = "Change Image Page"
    bl_description = "Show the previous or next page of generated images"

    direction: IntProperty(default=1, min=-1, max=1)

    def execute(self, context):
        state = context.window_manager.remi_image
        state.page = max(0, state.page + self.direction)
        return {"FINISHED"}


classes = (
    Remi_OT_GenerateImage,
    Remi_OT_CancelImage,
    Remi_OT_DownloadIris,
    Remi_OT_AddImage,
    Remi_OT_SelectImage,
    Remi_OT_UseImageFor3D,
    Remi_OT_OpenImage,
    Remi_OT_DeleteImage,
    Remi_OT_RegenerateImage,
    Remi_OT_CopyPrompt,
    Remi_OT_RefreshImages,
    Remi_OT_ImagePage,
)
