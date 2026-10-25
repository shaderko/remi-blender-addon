"""Non-blocking local generation client with an optional warm worker."""
from datetime import datetime
import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from .worker import COMPONENTS


class GenerationServer:
    def __init__(self, executable, models, cache_dir):
        self.executable = str(executable)
        self.models = Path(models).resolve()
        self.cache_dir = Path(cache_dir).resolve()
        self.process = None
        self.log_path = self.cache_dir / "server.log"
        self._log = None

    def matches(self, executable, models):
        return self.executable == str(executable) and self.models == Path(models)

    def start(self, idle_timeout):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        queue = self.cache_dir / "queue"
        queue.mkdir(parents=True, exist_ok=True)
        # A dead server can leave queued work behind. Never replay an old
        # request when a new warm process is started.
        for stale in list(queue.glob("*.json")) + list(queue.glob("*.tmp")):
            stale.unlink(missing_ok=True)
        self._log = self.log_path.open("ab")
        worker = Path(__file__).with_name("worker.py")
        try:
            self.process = subprocess.Popen(
                [
                    self.executable,
                    "-I",
                    "-B",
                    "-u",
                    str(worker),
                    "--serve",
                    "--cache-dir",
                    str(self.cache_dir),
                    "--models",
                    str(self.models),
                    "--idle-timeout",
                    str(max(0.0, float(idle_timeout))),
                ],
                cwd=self.cache_dir,
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            self._close_log()
            raise

    def enqueue(self, request, idle_timeout):
        queue = self.cache_dir / "queue"
        queue.mkdir(parents=True, exist_ok=True)
        destination = queue / f"{uuid.uuid4().hex}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "request": str(Path(request).resolve()),
                    "idle_timeout": max(0.0, float(idle_timeout)),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def shutdown(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None
        self._close_log()
        queue = self.cache_dir / "queue"
        for stale in list(queue.glob("*.json")) + list(queue.glob("*.tmp")):
            stale.unlink(missing_ok=True)

    def _close_log(self):
        if self._log is not None and not self._log.closed:
            self._log.close()
        self._log = None


_SERVER = None


def _default_cache_dir():
    return Path(tempfile.gettempdir()) / f"remi-generation-{os.getpid()}"


def _executable(python):
    executable = shutil.which(python) if python else None
    if executable is None or not Path(executable).is_file():
        raise ValueError("Choose an external Python executable in generation settings")
    return Path(executable)


def _ensure_server(python, models, cache_dir, idle_timeout):
    global _SERVER
    executable = _executable(python)
    models = Path(models).resolve()
    cache_dir = Path(cache_dir).resolve()
    if (
        _SERVER is not None
        and _SERVER.matches(executable, models)
        and _SERVER.alive()
    ):
        return _SERVER
    if _SERVER is not None:
        _SERVER.shutdown()
    _SERVER = GenerationServer(executable, models, cache_dir)
    _SERVER.start(idle_timeout)
    return _SERVER


def shutdown_generation_server():
    global _SERVER
    if _SERVER is not None:
        _SERVER.shutdown()
        _SERVER = None


atexit.register(shutdown_generation_server)


class GenerationJob:
    def __init__(self, process, directory, log, *, persistent=False):
        self.process = process
        self.directory = Path(directory)
        self._log = log
        self._persistent = bool(persistent)
        self._queue_file = None

    @classmethod
    def start(
        cls,
        *,
        python,
        models,
        image,
        output,
        gaussians,
        seed,
        cache_minutes=0,
        cache_dir=None,
    ):
        executable = _executable(python)
        models, image, output = Path(models), Path(image), Path(output)
        if not image.is_file():
            raise ValueError(f"Input image not found: {image}")
        missing = [name for name in COMPONENTS if not (models / name).is_file()]
        if missing:
            raise ValueError(f"Missing model files: {', '.join(missing)}. Use Download Models in generation settings.")
        if int(gaussians) not in (32768, 65536, 131072, 262144):
            raise ValueError("Unsupported Gaussian count")
        if not 0 <= int(seed) <= 2147483647:
            raise ValueError("Seed must be between 0 and 2147483647")
        cache_minutes = max(0, int(cache_minutes))
        directory = output / (
            datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        )
        directory.mkdir(parents=True, exist_ok=False)
        request = directory / "request.json"
        request.write_text(
            json.dumps(
                {
                    "models": str(models.resolve()),
                    "image": str(image.resolve()),
                    "gaussians": int(gaussians),
                    "seed": int(seed),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if cache_minutes > 0:
            idle_seconds = float(cache_minutes * 60)
            cache_dir = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
            server = _ensure_server(executable, models, cache_dir, idle_seconds)
            log_path = directory / "worker.log"
            log_path.write_text(
                f"Persistent generation worker\nServer log: {server.log_path}\n",
                encoding="utf-8",
            )
            job = cls(server.process, directory, None, persistent=True)
            job._queue_file = server.enqueue(request, idle_seconds)
            return job

        shutdown_generation_server()
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
        return cls(process, directory, log, persistent=False)

    def status(self):
        try:
            return json.loads((self.directory / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"phase": "Starting Python worker"}

    def _complete(self):
        try:
            complete = json.loads(
                (self.directory / "complete.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return False
        return complete.get("schema") == 1 and bool(complete.get("complete"))

    def _close_log(self):
        if self._log is not None and not self._log.closed:
            self._log.close()
        self._log = None

    def poll(self):
        if self._persistent:
            if self._complete():
                self._close_log()
                return 0
            if self.status().get("error"):
                self._close_log()
                return 1
            code = self.process.poll()
            if code is not None:
                self._close_log()
                return code if code != 0 else 1
            return None
        code = self.process.poll()
        if code is not None:
            self._close_log()
        return code

    def result(self):
        code = self.poll()
        if code is None:
            raise RuntimeError("Generation has not finished")
        if code != 0:
            error = self.status().get("error", f"Worker exited with code {code}")
            raise RuntimeError(f"{error}. See {self.directory / 'worker.log'}")
        if not self._complete():
            raise RuntimeError(
                f"Worker did not confirm completion. See {self.directory / 'worker.log'}"
            )
        result = self.directory / "splats.npz"
        if not result.is_file():
            raise RuntimeError(f"Worker did not produce {result}")
        return result

    def cancel(self):
        if self._persistent:
            if not self._complete():
                if self._queue_file is not None:
                    try:
                        self._queue_file.unlink(missing_ok=True)
                    except OSError:
                        pass
                shutdown_generation_server()
            self._close_log()
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._close_log()
        # Keep logs and any partial output for diagnosis; never delete user data.


class ModelDownloadJob(GenerationJob):
    """Use the same cancellable process boundary without loading inference packages."""

    @classmethod
    def start(cls, *, python, models, base_url):
        from .download import model_base

        executable = _executable(python)
        base_url = model_base(base_url)
        directory = Path(tempfile.mkdtemp(prefix="remi-model-download-"))
        token = uuid.uuid4().hex
        models = Path(models).resolve()
        log = (directory / "worker.log").open("wb")
        try:
            process = subprocess.Popen(
                [str(executable), "-I", "-B", "-u",
                 str(Path(__file__).with_name("download.py")),
                 "--base-url", base_url, "--models", str(models),
                 "--status-dir", str(directory), "--token", token],
                cwd=directory, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            log.close()
            raise
        job = cls(process, directory, log)
        job.models, job.token = models, token
        return job

    def result(self):
        code = self.poll()
        if code is None:
            raise RuntimeError("Model download has not finished")
        if code != 0 or not self._complete():
            error = self.status().get("error", "Model download did not complete")
            raise RuntimeError(f"{error}. See {self.directory / 'worker.log'}")
        return self.models

    def cancel(self):
        from .download import partial_path

        super().cancel()
        # Only delete this process's incomplete files, never another job's files.
        for name in COMPONENTS:
            try:
                partial_path(self.models, name, self.token).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
