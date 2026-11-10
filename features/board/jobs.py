"""Local file-in/file-out board jobs; no foreground object selection required."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

import bpy

from ...integrations.iris3b.client import shutdown_iris_server
from ...integrations.triposplat.client import GenerationJob, shutdown_generation_server
from ...integrations.splat_mesh.client import MeshJob
from ...integrations.splat_mesh.runtime import terminate_tree
from ..generation.settings import default_python


class LocalBlenderJob:
    def __init__(self, process, directory, log):
        self.process, self.directory, self.log = process, directory, log

    @classmethod
    def start(cls, *, output, request):
        directory = Path(output) / ("board_" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        request = dict(request)
        spec = importlib.util.find_spec("pymeshlab")
        request["dependency_paths"] = [str(Path(spec.origin).parent.parent)] if spec and spec.origin else []
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request, allow_nan=False), encoding="utf-8")
        log = (directory / "worker.log").open("wb")
        try:
            process = subprocess.Popen([bpy.app.binary_path, "--background", "--factory-startup",
                "--python-exit-code", "1", "--python", str(Path(__file__).with_name("local_worker.py")),
                "--", "--request", str(request_path)], stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, cwd=directory,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name != "nt")
        except Exception:
            log.close()
            raise
        return cls(process, directory, log)

    def status(self):
        try:
            return json.loads((self.directory / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"phase": "Starting local Blender worker"}

    def poll(self):
        code = self.process.poll()
        if code is not None:
            self.log.close()
        return code

    def result(self):
        if self.poll() != 0:
            raise RuntimeError(self.status().get("error", f"Local job failed; see {self.directory / 'worker.log'}"))
        result = json.loads((self.directory / "result.json").read_text(encoding="utf-8"))
        artifact = Path(result["artifact"]).resolve()
        if result.get("schema") != 1 or not artifact.is_relative_to(self.directory.resolve()) or not artifact.is_file():
            raise ValueError("Local worker returned an invalid artifact")
        return artifact

    def cancel(self):
        if self.process.poll() is None:
            (self.directory / "cancel.request").touch()
            terminate_tree(self.process, group=True)
        self.log.close()


def busy_elsewhere(context):
    wm = context.window_manager
    return (wm.remi_generation.busy or wm.remi_generation.mesh_busy or wm.remi_image.busy
            or wm.remi_texture.busy or wm.remi_session.active)


class Runner:
    def __init__(self):
        self.job = None
        self.asset_id = ""
        self.preview_job = None
        self.preview_id = ""

    def start(self, context, catalog, asset):
        parent = catalog.assets[asset.parent]
        mesh_input = asset.settings.get("mesh_artifact", parent.artifact)
        if not Path(mesh_input).is_file():
            raise ValueError("The source asset file is missing")
        options = asset.settings
        if not isinstance(options.get("output"), str) or not options["output"].strip():
            raise ValueError("Choose an output folder in generation settings")
        if asset.operation == "GENERATE_3D":
            shutdown_iris_server()
            self.job = GenerationJob.start(python=options["python"], models=options["models"],
                image=parent.artifact, output=options["output"], gaussians=options["gaussians"], seed=options["seed"],
                cache_minutes=options["cache_minutes"], cache_dir=options["cache_dir"])
            state = context.window_manager.remi_generation
            state.busy, state.source_image = True, parent.artifact
            state.status_kind = "PROGRESS"
        elif asset.operation == "CREATE_MESH":
            shutdown_iris_server()
            shutdown_generation_server()
            self.job = MeshJob.start(python=options["python"], archive=parent.artifact,
                output=options["output"], quality=options["quality"], source=parent.id)
            context.window_manager.remi_generation.mesh_busy = True
        elif asset.operation == "FLOW":
            self.job = LocalBlenderJob.start(output=options["output"], request={
                "operation": "FLOW", "input": parent.artifact, "flow": options["flow"],
                "yaw": asset.yaw, "elevation": asset.elevation})
        elif asset.operation == "TEXTURE":
            if not Path(options["gaussian_source"]).is_file():
                raise ValueError("The linked Gaussian texture source is missing")
            self.job = LocalBlenderJob.start(output=options["output"], request={
                "operation": "TEXTURE", "input": mesh_input, "python": options["python"],
                "gaussian_source": options["gaussian_source"], "yaw": asset.yaw,
                "elevation": asset.elevation, "texture": options["texture"]})
        else:
            raise ValueError("Unsupported board action")
        self.asset_id = asset.id
        asset.directory = str(self.job.directory)
        asset.state, asset.status = "RUNNING", "Starting…"

    def release_state(self, context, asset):
        kind = {"READY": "SUCCESS", "ERROR": "ERROR", "CANCELLED": "CANCELLED"}.get(asset.state, "IDLE")
        if asset.operation == "GENERATE_3D":
            state = context.window_manager.remi_generation
            state.busy = False
            state.status, state.status_kind, state.progress = asset.status, kind, asset.progress
        elif asset.operation == "CREATE_MESH":
            state = context.window_manager.remi_generation
            state.mesh_busy = False
            state.mesh_status, state.mesh_status_kind = asset.status, kind

    def tick(self, context, catalog):
        changed = False
        if self.job is not None:
            asset = catalog.assets[self.asset_id]
            status = self.job.status()
            asset.status = str(status.get("phase", "Working…"))
            try:
                asset.progress = max(0, min(1, float(status.get("progress", float(status.get("percent", 0))/100))))
            except (ValueError, TypeError):
                pass
            if asset.operation == "GENERATE_3D":
                from ..generation.operators import _progress_fraction
                asset.progress = _progress_fraction(asset.status)
                context.window_manager.remi_generation.status = asset.status
                context.window_manager.remi_generation.progress = asset.progress
            if self.job.poll() is not None:
                try:
                    artifact = self.job.result()
                    asset.artifact = str(artifact)
                    asset.preview = str(artifact.with_name("preview.png")) if artifact.with_name("preview.png").is_file() else ""
                    asset.state, asset.status, asset.progress = "READY", "Ready", 1.0
                    if isinstance(self.job, LocalBlenderJob):
                        result = json.loads((self.job.directory / "result.json").read_text(encoding="utf-8"))
                        if result.get("preview_error"):
                            asset.status = f"Preview unavailable: {result['preview_error']}"
                except Exception as exc:
                    asset.state, asset.status = "ERROR", str(exc)
                finally:
                    self.release_state(context, asset)
                    self.job, self.asset_id = None, ""
                changed = True
        if self.job is None and not busy_elsewhere(context):
            queued = next((asset for asset in catalog.assets.values() if asset.state == "QUEUED" and asset.operation), None)
            if queued is not None:
                try:
                    self.start(context, catalog, queued)
                except Exception as exc:
                    queued.state, queued.status = "ERROR", str(exc)
                changed = True
        if self.preview_job is not None and self.preview_job.poll() is not None:
            asset = catalog.assets.get(self.preview_id)
            if asset is not None:
                try:
                    asset.preview = str(self.preview_job.result())
                except Exception as exc:
                    asset.status = f"Preview unavailable: {exc}"
            self.preview_job, self.preview_id = None, ""
            changed = True
        return changed

    def request_mesh_preview(self, asset, output):
        if self.preview_job is None and asset.kind == "MESH" and asset.state == "READY" and not asset.preview:
            # A failed preview is explicit and is not retried every timer tick.
            if asset.status.startswith("Preview unavailable"):
                return
            self.preview_job = LocalBlenderJob.start(output=output, request={"operation": "PREVIEW",
                "input": asset.artifact, "yaw": asset.yaw, "elevation": asset.elevation})
            self.preview_id = asset.id

    def cancel(self, context, catalog, identifier):
        asset = catalog.assets.get(identifier)
        if asset is None or asset.state not in {"QUEUED", "RUNNING"}:
            return
        if identifier == self.asset_id and self.job is not None:
            self.job.cancel()
            asset.state, asset.status = "CANCELLED", "Cancelled; source and completed files are kept"
            self.release_state(context, asset)
            self.job, self.asset_id = None, ""
        asset.state, asset.status = "CANCELLED", "Cancelled; source and completed files are kept"

    def close(self, context, catalog):
        if self.asset_id:
            self.cancel(context, catalog, self.asset_id)
        if self.preview_job is not None:
            self.preview_job.cancel()
            self.preview_job, self.preview_id = None, ""


def generation_options(context):
    settings = context.scene.remi_generation
    return {"python": bpy.path.abspath(settings.python) if settings.python else default_python(),
            "models": bpy.path.abspath(settings.models), "output": bpy.path.abspath(settings.output),
            "gaussians": int(settings.gaussians), "seed": settings.seed,
            "cache_minutes": settings.model_cache_minutes,
            "cache_dir": str(Path(bpy.app.tempdir or tempfile.gettempdir()) / "remi-generation-cache")}
