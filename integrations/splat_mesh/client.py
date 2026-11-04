"""Non-blocking local Gaussian-to-mesh worker client (stdlib only)."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from .runtime import sha256, terminate_tree

WORKER = "splatmesh_worker.py"


class MeshJob:
    def __init__(self, process, directory, log):
        self.process, self.directory, self._log = process, directory, log

    @classmethod
    def start(cls, *, python, archive, output, quality="DENSE", source=None):
        executable = shutil.which(python) if python else None
        if executable is None or not Path(executable).is_file():
            raise ValueError("Choose an external Python with remi-splat-mesh installed")
        archive, output = Path(archive), Path(output)
        if not archive.is_file():
            raise ValueError(f"Gaussian archive not found: {archive}")
        if quality not in {"DENSE", "FAST"}:
            raise ValueError("Unsupported reconstruction quality")
        directory = output / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        request = directory / "request.json"
        request.write_text(json.dumps({
            "archive": str(archive.resolve()),
            "source": str(source or ""),
            "quality": quality,
        }, indent=2), encoding="utf-8")
        log = (directory / "worker.log").open("wb")
        try:
            process = subprocess.Popen(
                [str(executable), "-I", "-B", "-u", str(Path(__file__).with_name(WORKER)),
                 "--request", str(request.resolve())],
                cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt",
            )
        except Exception:
            log.close()
            raise
        return cls(process, directory, log)

    def status(self):
        try:
            return json.loads((self.directory / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"phase": "Starting mesher"}

    def poll(self):
        code = self.process.poll()
        if code is not None:
            self._log.close()
        return code

    def result(self):
        code = self.poll()
        if code is None:
            raise RuntimeError("Mesh conversion has not finished")
        if code != 0:
            error = self.status().get("error", f"Worker exited with code {code}")
            raise RuntimeError(f"{error}. See {self.directory / 'worker.log'}")
        try:
            complete = json.loads((self.directory / "complete.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            complete = {}
        if complete.get("schema") != 1 or not complete.get("complete"):
            raise RuntimeError(f"Mesher did not confirm completion. See {self.directory / 'worker.log'}")
        mesh = self.directory / "mesh.ply"
        if not mesh.is_file():
            raise RuntimeError(f"Mesher did not produce {mesh}")
        try:
            report = json.loads((self.directory / "report.json").read_text(encoding="utf-8"))
            valid = (report.get("schema") == 2 and report.get("quality") in {"DENSE", "FAST"}
                     and isinstance(report.get("vertices"), int) and report["vertices"] > 0
                     and isinstance(report.get("triangles"), int) and report["triangles"] > 0
                     and report.get("mesh_sha256") == sha256(mesh))
        except (OSError, ValueError, AttributeError):
            valid = False
        if not valid:
            raise RuntimeError("Mesh report is missing, invalid, or does not match the output. "
                               f"See {self.directory / 'worker.log'}")
        return mesh

    def cancel(self):
        if self.process.poll() is None:
            (self.directory / "cancel.request").touch()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                terminate_tree(self.process, group=True)
        self._log.close()
