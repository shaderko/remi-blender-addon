"""Non-blocking Iris-3B client with an optional warm external worker."""

from datetime import datetime
import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

try:
    from .constants import MODEL_FILES, MODEL_LABELS, TEXT_ENCODER_FILES
except ImportError:  # direct import in a small test harness
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from constants import MODEL_FILES, MODEL_LABELS, TEXT_ENCODER_FILES


def _executable(python):
    executable = shutil.which(python) if python else None
    if executable is None or not Path(executable).is_file():
        raise ValueError("Choose the external CUDA/PyTorch Python in the Images settings")
    return Path(executable)


def missing_components(models, variant):
    models = Path(models)
    required = [models / "iris-3b" / name for name in MODEL_FILES[variant]]
    required += [models / "Qwen3-VL-4B-Instruct" / name for name in TEXT_ENCODER_FILES]
    return [str(path.relative_to(models)) for path in required if not path.is_file()]


class IrisServer:
    def __init__(self, executable, models, cache_dir, variant, memory_mode):
        self.executable = str(executable)
        self.models = Path(models).resolve()
        self.cache_dir = Path(cache_dir).resolve()
        self.variant = variant
        self.memory_mode = memory_mode
        self.process = None
        self.log_path = self.cache_dir / "server.log"
        self._log = None

    def matches(self, executable, models, variant, memory_mode):
        return (
            self.executable == str(executable)
            and self.models == Path(models).resolve()
            and self.variant == variant
            and self.memory_mode == memory_mode
        )

    def start(self, idle_timeout):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        queue = self.cache_dir / "queue"
        queue.mkdir(parents=True, exist_ok=True)
        for stale in list(queue.glob("*.json")) + list(queue.glob("*.tmp")):
            stale.unlink(missing_ok=True)
        self._log = self.log_path.open("ab")
        worker = Path(__file__).with_name("worker.py")
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
                "--variant",
                self.variant,
                "--memory-mode",
                self.memory_mode,
                "--idle-timeout",
                str(max(0.0, float(idle_timeout))),
            ],
            cwd=self.cache_dir,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def enqueue(self, request, idle_timeout):
        queue = self.cache_dir / "queue"
        queue.mkdir(parents=True, exist_ok=True)
        destination = queue / f"{uuid.uuid4().hex}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"request": str(Path(request).resolve()), "idle_timeout": max(0.0, float(idle_timeout))},
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
    return Path(tempfile.gettempdir()) / f"remi-iris-{os.getpid()}"


def _ensure_server(executable, models, variant, memory_mode, cache_dir, idle_timeout):
    global _SERVER
    if (
        _SERVER is not None
        and _SERVER.matches(executable, models, variant, memory_mode)
        and _SERVER.alive()
    ):
        return _SERVER
    if _SERVER is not None:
        _SERVER.shutdown()
    _SERVER = IrisServer(executable, models, cache_dir, variant, memory_mode)
    _SERVER.start(idle_timeout)
    return _SERVER


def shutdown_iris_server():
    global _SERVER
    if _SERVER is not None:
        _SERVER.shutdown()
        _SERVER = None


atexit.register(shutdown_iris_server)


class IrisJob:
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
        output,
        variant,
        prompt,
        negative_prompt="",
        width=1024,
        height=1024,
        steps=100,
        cfg_scale=3.0,
        seed=0,
        count=1,
        memory_mode="auto",
        cache_minutes=0,
        cache_dir=None,
    ):
        executable = _executable(python)
        models = Path(models).resolve()
        output = Path(output).resolve()
        if variant not in MODEL_FILES:
            raise ValueError("Choose Iris-3B or Iris-3B Turbo")
        missing = missing_components(models, variant)
        if missing:
            raise ValueError(
                f"Missing model files: {', '.join(missing[:3])}. Use Download Model in the Images tab."
            )
        prompt = str(prompt).strip()
        if not prompt:
            raise ValueError("Enter an image prompt")
        if not 1 <= int(count) <= 8:
            raise ValueError("Image count must be between 1 and 8")
        if not 0 <= int(seed) <= 2147483647:
            raise ValueError("Seed must be between 0 and 2147483647")
        directory = output / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        request = directory / "request.json"
        request.write_text(
            json.dumps(
                {
                    "models": str(models),
                    "variant": variant,
                    "prompt": prompt,
                    "negative_prompt": str(negative_prompt),
                    "width": int(width),
                    "height": int(height),
                    "steps": int(steps),
                    "cfg_scale": float(cfg_scale),
                    "seed": int(seed),
                    "count": int(count),
                    "memory_mode": memory_mode,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        cache_minutes = max(0, int(cache_minutes))
        if cache_minutes > 0:
            idle_seconds = float(cache_minutes * 60)
            server = _ensure_server(
                executable, models, variant, memory_mode,
                Path(cache_dir) if cache_dir is not None else _default_cache_dir(),
                idle_seconds,
            )
            log_path = directory / "worker.log"
            log_path.write_text(
                f"Persistent Iris worker\nServer log: {server.log_path}\n", encoding="utf-8"
            )
            job = cls(server.process, directory, None, persistent=True)
            job._queue_file = server.enqueue(request, idle_seconds)
            return job

        shutdown_iris_server()
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
                    "--models",
                    str(models),
                    "--variant",
                    variant,
                    "--memory-mode",
                    memory_mode,
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
            return {"phase": "Starting Iris worker"}

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
            raise RuntimeError("Image generation has not finished")
        if code != 0:
            error = self.status().get("error", f"Worker exited with code {code}")
            raise RuntimeError(f"{error}. See {self.directory / 'worker.log'}")
        if not self._complete():
            raise RuntimeError(
                f"Worker did not confirm completion. See {self.directory / 'worker.log'}"
            )
        return self.directory

    def cancel(self):
        if self._persistent:
            if not self._complete():
                if self._queue_file is not None:
                    try:
                        self._queue_file.unlink(missing_ok=True)
                    except OSError:
                        pass
                shutdown_iris_server()
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


class IrisDownloadJob(IrisJob):
    @classmethod
    def start(cls, *, python, models, variant, text_encoder=True):
        executable = _executable(python)
        models = Path(models).resolve()
        directory = Path(tempfile.mkdtemp(prefix="remi-iris-download-"))
        log = (directory / "worker.log").open("wb")
        try:
            process = subprocess.Popen(
                [
                    str(executable),
                    "-I",
                    "-B",
                    "-u",
                    str(Path(__file__).with_name("download.py")),
                    "--models", str(models),
                    "--variant", variant,
                    "--status-dir", str(directory),
                ]
                + (["--text-encoder"] if text_encoder else []),
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            log.close()
            raise
        job = cls(process, directory, log)
        job.models, job.variant = models, variant
        return job

    def result(self):
        code = self.poll()
        if code is None:
            raise RuntimeError("Model download has not finished")
        if code != 0 or not self._complete():
            error = self.status().get("error", "Model download did not complete")
            raise RuntimeError(f"{error}. See {self.directory / 'worker.log'}")
        return self.models


__all__ = [
    "IrisDownloadJob",
    "IrisJob",
    "MODEL_LABELS",
    "missing_components",
    "shutdown_iris_server",
]
