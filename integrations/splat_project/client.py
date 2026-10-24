"""Non-blocking local splat-to-texture worker client (stdlib only)."""
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import uuid


class ProjectionJob:
    def __init__(self, process, directory, log):
        self.process = process
        self.directory = directory
        self._log = log

    @classmethod
    def start(
        cls,
        *,
        python,
        archive,
        mesh,
        output,
        texture_size=2048,
        samples=16,
        normal_source="AUTO",
        normal_strength=1.0,
        normal_smoothing=2,
        fill_holes=True,
        fill_background=True,
        margin_px=8,
        source=None,
    ):
        executable = shutil.which(python) if python else None
        if executable is None or not Path(executable).is_file():
            raise ValueError("Choose an external Python executable with NumPy, SciPy and Pillow installed")
        archive, mesh, output = Path(archive), Path(mesh), Path(output)
        if not archive.is_file():
            raise ValueError(f"Gaussian archive not found: {archive}")
        if not mesh.is_file():
            raise ValueError(f"Mesh archive not found: {mesh}")
        if int(texture_size) not in (512, 1024, 2048, 4096, 8192):
            raise ValueError("Unsupported texture size")
        if not 4 <= int(samples) <= 64:
            raise ValueError("Unsupported sample count")
        if normal_source not in {"AUTO", "GRADIENT", "COVARIANCE", "MESH"}:
            raise ValueError("Unsupported normal source")
        if not 0.0 <= float(normal_strength) <= 1.0:
            raise ValueError("Normal strength must be in [0, 1]")
        if not 0 <= int(normal_smoothing) <= 16:
            raise ValueError("Normal smoothing must be between 0 and 16 pixels")
        if not 0 <= int(margin_px) <= 256:
            raise ValueError("Margin must be between 0 and 256 pixels")

        directory = output / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        request = directory / "request.json"
        request.write_text(
            json.dumps(
                {
                    "archive": str(archive.resolve()),
                    "mesh": str(mesh.resolve()),
                    "source": str(source or ""),
                    "texture_size": int(texture_size),
                    "samples": int(samples),
                    "normal_source": str(normal_source),
                    "normal_strength": float(normal_strength),
                    "normal_smoothing": int(normal_smoothing),
                    "fill_holes": bool(fill_holes),
                    "fill_background": bool(fill_background),
                    "margin_px": int(margin_px),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log = (directory / "worker.log").open("wb")
        try:
            process = subprocess.Popen(
                [
                    str(executable),
                    "-I",
                    "-B",
                    "-u",
                    str(Path(__file__).with_name("worker.py")),
                    "--request",
                    str(request.resolve()),
                ],
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
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
            return {"phase": "Starting projector"}

    def poll(self):
        code = self.process.poll()
        if code is not None:
            self._log.close()
        return code

    def result(self):
        code = self.poll()
        if code is None:
            raise RuntimeError("Texture projection has not finished")
        if code != 0:
            error = self.status().get("error", f"Worker exited with code {code}")
            raise RuntimeError(f"{error}. See {self.directory / 'worker.log'}")
        try:
            complete = json.loads((self.directory / "complete.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            complete = {}
        if complete.get("schema") != 1 or not complete.get("complete"):
            raise RuntimeError(
                f"Projector did not confirm completion. See {self.directory / 'worker.log'}"
            )
        paths = {
            name: self.directory / f"{name}.png"
            for name in ("albedo", "normal", "confidence")
        }
        for name, path in paths.items():
            if not path.is_file():
                raise RuntimeError(f"Projector did not produce {path}")
        report_path = self.directory / "report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
        return {
            **{name: str(path) for name, path in paths.items()},
            "report": str(report_path),
            "directory": str(self.directory),
            "report_data": report,
        }

    def cancel(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._log.close()
