"""Nonblocking Blender adapter for an isolated OAuth connection with optional OS-protected persistence."""
import atexit
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from ..remi_cloud.client import bundled_python


class CodexSession:
    def __init__(self, python=None):
        self.python = python
        self.process = None
        self.pending = None
        self.signed_in = False
        self.retired = []
        self.credential_root = None

    def start(self, *, action, output, **options):
        if self.pending is not None and self.pending.poll() is None:
            raise RuntimeError("Wait for the current Codex request or cancel it first.")
        for process in self.retired[:]:
            if process.poll() is not None:
                self.retired.remove(process)
        if self.process is None or self.process.poll() is not None:
            self.signed_in = False
            if self.process is not None and self.process.stdin is not None:
                self.process.stdin.close()
            self.process = subprocess.Popen(
                [self.python or bundled_python(), "-I", "-B", "-u", str(Path(__file__).with_name("worker.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "HOME", "USERPROFILE"}},
            )
        if action in {"sign_in", "restore", "disconnect"}:
            directory = Path(tempfile.mkdtemp(prefix="remi-codex-signin-"))
        else:
            directory = Path(output).resolve() / ("codex_" + uuid.uuid4().hex)
            directory.mkdir(parents=True)
        os.chmod(directory, 0o700)
        self.pending = job = CodexJob(self, directory, action)
        try:
            self.process.stdin.write((json.dumps({**options, "action": action, "directory": str(directory),
                **({"credential_root": str(self.credential_root)} if self.credential_root else {})}) + "\n").encode())
            self.process.stdin.flush()
        except (OSError, BrokenPipeError):
            self.shutdown()
            raise RuntimeError("The local Codex worker stopped. Try signing in again.") from None
        return job

    def shutdown(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                self.retired.append(self.process)
            if self.process.stdin is not None:
                self.process.stdin.close()
        self.process = None
        self.pending = None
        self.signed_in = False


class CodexJob:
    backend = "CODEX"

    def __init__(self, session, directory, action):
        self.session, self.directory, self.action = session, directory, action
        self.process = session.process
        self.cancelled = False

    def status(self):
        try:
            return json.loads((self.directory / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"phase": "Starting local Codex connection…"}

    def poll(self):
        if self.cancelled:
            return 1
        value = self.status()
        if value.get("done"):
            self.session.signed_in = bool(value.get("signed_in"))
            return 1 if value.get("error") else 0
        if self.process is None or self.process.poll() is not None:
            return 1
        return None

    def result(self):
        code = self.poll()
        if code is None:
            raise RuntimeError("Codex has not finished yet.")
        if code != 0:
            raise RuntimeError(self.status().get("error", "The local Codex worker stopped. Sign in again."))
        if self.action == "generate" and not (self.directory / "complete.json").is_file():
            raise RuntimeError("Codex did not confirm image completion.")
        return self.directory

    def cancel(self):
        if self.poll() is None:
            self.cancelled = True
            self.session.shutdown()
        if self.session.pending is self:
            self.session.pending = None


session = CodexSession()
atexit.register(session.shutdown)
