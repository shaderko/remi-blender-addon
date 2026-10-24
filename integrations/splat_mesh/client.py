"""Non-blocking local Gaussian-to-mesh worker client (stdlib only)."""
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import uuid


class MeshJob:
    def __init__(self, process, directory, log):
        self.process, self.directory, self._log = process, directory, log

    @classmethod
    def start(cls, *, python, archive, output, resolution=224, taubin=12,
              min_component=500, source=None):
        executable = shutil.which(python) if python else None
        if executable is None or not Path(executable).is_file():
            raise ValueError("Choose an external Python executable with PyTorch and scipy installed")
        archive, output = Path(archive), Path(output)
        if not archive.is_file():
            raise ValueError(f"Gaussian archive not found: {archive}")
        if int(resolution) not in (128, 160, 192, 224, 256, 288):
            raise ValueError("Unsupported mesh resolution")
        if int(taubin) not in (0, 6, 12, 20):
            raise ValueError("Unsupported smoothing amount")
        directory = output / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        request = directory / "request.json"
        request.write_text(json.dumps({
            "archive": str(archive.resolve()),
            "source": str(source or ""),
            "resolution": int(resolution),
            "taubin": int(taubin),
            "min_component": int(min_component),
        }, indent=2), encoding="utf-8")
        log = (directory / "worker.log").open("wb")
        try:
            process = subprocess.Popen(
                [str(executable), "-I", "-B", "-u", str(Path(__file__).with_name("worker.py")),
                 "--request", str(request.resolve())],
                cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
        return mesh

    def cancel(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._log.close()
