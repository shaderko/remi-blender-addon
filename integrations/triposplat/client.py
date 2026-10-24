"""Non-blocking, file-based local worker client (stdlib only)."""
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import uuid

from .worker import COMPONENTS


class GenerationJob:
    def __init__(self, process, directory, log):
        self.process, self.directory, self._log = process, directory, log

    @classmethod
    def start(cls, *, python, models, image, output, gaussians, seed):
        executable = shutil.which(python) if python else None
        if executable is None or not Path(executable).is_file():
            raise ValueError("Choose an external Python executable with CUDA PyTorch installed")
        models, image, output = Path(models), Path(image), Path(output)
        if not image.is_file():
            raise ValueError(f"Input image not found: {image}")
        missing = [name for name in COMPONENTS if not (models / name).is_file()]
        if missing:
            raise ValueError(f"Missing model files: {', '.join(missing)}")
        if int(gaussians) not in (32768, 65536, 131072, 262144):
            raise ValueError("Unsupported Gaussian count")
        if not 0 <= int(seed) <= 2147483647:
            raise ValueError("Seed must be between 0 and 2147483647")
        directory = output / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        request = directory / "request.json"
        request.write_text(json.dumps({
            "models": str(models.resolve()), "image": str(image.resolve()),
            "gaussians": int(gaussians), "seed": int(seed),
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
            return {"phase": "Starting Python worker"}

    def poll(self):
        code = self.process.poll()
        if code is not None:
            self._log.close()
        return code

    def result(self):
        code = self.poll()
        if code is None:
            raise RuntimeError("Generation has not finished")
        if code != 0:
            error = self.status().get("error", f"Worker exited with code {code}")
            raise RuntimeError(f"{error}. See {self.directory / 'worker.log'}")
        try:
            complete = json.loads((self.directory / "complete.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            complete = {}
        if complete.get("schema") != 1 or not complete.get("complete"):
            raise RuntimeError(f"Worker did not confirm completion. See {self.directory / 'worker.log'}")
        result = self.directory / "splats.npz"
        if not result.is_file():
            raise RuntimeError(f"Worker did not produce {result}")
        return result

    def cancel(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._log.close()
        # Keep logs and any partial output for diagnosis; never delete user data.
