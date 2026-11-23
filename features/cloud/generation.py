"""Existing generation-job interface backed by the authenticated Remi SDK.

The image/3D operators and board queue keep their settings and result handling.
No provider credentials or duplicate model settings live here.
"""
from pathlib import Path
import json
import uuid

import bpy

from ...integrations.remi_cloud.client import runtime
from ...integrations.remi_cloud.protocol import CloudError


def is_api(context, operation=None):
    if operation == "generate":
        from ..generation.settings import execution_backend
        return execution_backend(context.scene.remi_generation) == "API"
    return getattr(context.scene.remi_generation, "backend", "LOCAL") == "API"


def available(context, operation=None):
    state = getattr(context.window_manager, "remi_cloud", None)
    value = runtime.status()
    if not state or not state.signed_in or value.get("signed_in") is not True or runtime.pending or runtime.following:
        return False
    workspace=runtime.workspace_status()
    if workspace and (workspace.get("project_id")!=getattr(state,"project","default") or
            (workspace.get("catalog",{}).get("projects") and not workspace.get("ready"))):
        return False
    caps = value.get("capabilities", {})
    return bool(caps.get("enabled") and (operation is None or caps.get("operations", {}).get(operation)))


def validate_settings(context, operation, options=None):
    if not available(context, operation):
        raise CloudError("Sign in to Remi Cloud from the account menu, then retry Generate.")
    state = context.window_manager.remi_cloud
    if state.request_path and state.request_path != runtime.completed_request:
        raise CloudError("Resume the previous API request from the account menu before generating again.")
    if operation == "generate":
        from ..generation.settings import execution_model
        model = (options or {}).get("model", execution_model(context.scene.remi_generation))
        if model != "idol":
            raise CloudError("NCM and TripoSplat run locally. Select IDOL · Cloud for Cloud generation.")
        advertised = runtime.status().get("capabilities", {}).get("modelGeneration", {}).get("model")
        if not isinstance(advertised, str) or advertised.lower() != "idol":
            raise CloudError("This Cloud server has not advertised IDOL. Refresh the account connection or choose a local engine.")
    else:
        settings = context.scene.remi_image
        if settings.model != "turbo":
            raise CloudError("The API currently runs Iris Turbo. Choose Turbo or switch to Local for Base.")
        from ..images.settings import dimensions
        if max(dimensions(settings)) > 1024:
            raise CloudError("This API test supports up to 1024 pixels per side. Choose Square or use Local for larger sizes.")
    return state


class ApiGenerationJob:
    """poll/status/result/cancel match existing local generation adapters."""
    backend = "API"
    def __init__(self, context, *, operation, output, options, prompt=None, image=None, count=1, selection=None):
        state = validate_settings(context, operation, selection or options)
        if not output or not str(output).strip():
            raise CloudError("Choose an output folder in generation settings.")
        self.directory = Path(output) / ("api_" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.operation, self.options = operation, dict(options)
        self.base_url, self.max_cost = state.base_url, state.max_cost_eur.strip() or "1.00"
        self.prompt, self.image, self.count = prompt, image, count
        self.index = 0
        self.images = []
        self.output_path = None
        self.finished = self.stopped = False
        self.error = None
        self.last = {}
        self._start()

    def _start(self):
        options = dict(self.options)
        if self.operation == "generate_image":
            options["seed"] = (options.get("seed", 0) + self.index) % 2147483648
        runtime.command("generate", self.base_url, operation=self.operation,
            prompt=self.prompt, image=self.image, output=str(self.directory),
            options=options, max_cost_eur=self.max_cost,project_id=(runtime.workspace_status().get("project_id") if runtime.workspace_status().get("project_id")!="default" else None))
        # The isolated SDK worker receives pushed events. Blender only reads
        # its local status file; no timer sends network status requests.
        runtime.following = False

    def _consume(self, value):
        path = Path(value.get("output_path", ""))
        if not path.is_file() or not value.get("request_path"):
            raise CloudError("The API result has no verified downloaded file.")
        directory = Path(value["request_path"]).resolve().parent
        from ...integrations.remi_cloud.jobs import get_json, read_file, sha, validate_output, OUTPUT_LIMIT
        intent = get_json(directory / "request.json")
        fact = get_json(directory / "complete.json")
        expected = "generation.png" if self.operation == "generate_image" else "generation.splat"
        if path.resolve() != directory / expected or fact.get("filename") != expected or intent["operation"] != self.operation:
            raise CloudError("The API result identity changed.")
        body = read_file(path, OUTPUT_LIMIT)
        if len(body) != fact["bytes"] or sha(body) != fact["sha256"]:
            raise CloudError("The downloaded API result changed.")
        validate_output(body, intent)
        if self.operation == "generate":
            self.output_path = path
            self.finished = True
            return
        destination = self.directory / f"image_{self.index:03d}.png"
        destination.write_bytes(body)
        self.images.append({"file": destination.name, "seed": intent["options"].get("seed", 0)})
        metadata = {"prompt": self.prompt, "negative_prompt": "", "model": "turbo", "model_label": "Iris-3B Turbo",
            "width": self.options["width"], "height": self.options["height"], "seed": self.options.get("seed", 0),
            "steps": 4, "cfg_scale": 1.0, "count": len(self.images), "images": self.images, "source": "api"}
        (self.directory / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
        (self.directory / "complete.json").write_text(json.dumps({"count": len(self.images)}), encoding="utf-8")
        from ..images.gallery import gallery
        gallery.invalidate()
        self.index += 1
        if self.index < self.count:
            self._start()
        else:
            self.output_path = self.directory
            self.finished = True

    def poll(self):
        if self.finished or self.stopped:
            return 1 if self.error or self.stopped else 0
        value = runtime.status()
        self.last = value
        state = value.get("state")
        try:
            if state == "job_ready" and not runtime.pending:
                self._consume(value)
            elif state in {"error", "job_failed", "signed_out"} and not runtime.pending:
                raise CloudError(value.get("message") or "The API request could not complete.")
        except (CloudError, OSError, ValueError, KeyError) as error:
            self.error = str(error)
            self.finished = True
        return (1 if self.error else 0) if self.finished else None

    def status(self):
        value = runtime.status()
        progress = (self.index + float(value.get("progress", 0))) / self.count
        return {"phase": self.error or "API · " + (value.get("message") or "Generating…"),
            "progress": progress, "percent": progress * 100}

    def result(self):
        if self.error:
            raise CloudError(self.error)
        if not self.finished or not self.output_path:
            raise CloudError("The API request is not complete. Resume its saved request before retrying.")
        return self.output_path

    def cancel(self):
        # Completed adapters are also released through the local runtime stop().
        if self.finished:
            return
        # Do not cancel remote execution or clear its recovery identity.
        self.stopped = True
        runtime.following = False
        runtime.paused = True
        state = getattr(bpy.context.window_manager, "remi_cloud", None)
        if state:
            state.message = "Stopped waiting. The API job continues; resume it from the account menu."


def start_3d(context, *, image, output, options=None):
    settings = context.scene.remi_generation
    source = dict(options or {})
    from ..generation.settings import execution_model
    selection = {"model": source.get("model", execution_model(settings))}
    return ApiGenerationJob(context, operation="generate", image=image, output=output, selection=selection,
        options={"seed": source.get("seed", settings.seed), "gaussianCount": int(source.get("gaussians", settings.gaussians))})


def start_image(context):
    settings = context.scene.remi_image
    validate_settings(context, "generate_image")
    from ..images.settings import dimensions
    width, height = dimensions(settings)
    return ApiGenerationJob(context, operation="generate_image", output=bpy.path.abspath(settings.output),
        prompt=settings.prompt, count=settings.count, options={"seed": settings.seed, "width": width, "height": height})


def draw_backend(layout, context):
    settings = context.scene.remi_generation
    layout.prop(settings, "use_cloud")
