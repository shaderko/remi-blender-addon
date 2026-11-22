"""Non-blocking Blender-side process adapter, with no bpy calls or network threads."""
import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import hashlib
import time
import uuid

from .protocol import DEFAULT_API, api_url


def bundled_python():
    names = ("python.exe",) if sys.platform == "win32" else (f"python{sys.version_info.major}.{sys.version_info.minor}","python3")
    for name in names:
        for path in (Path(sys.prefix)/"bin"/name, Path(sys.prefix)/name):
            if path.is_file(): return str(path)
    raise RuntimeError("Blender's bundled Python executable was not found.")


class CloudProcess:
    def __init__(self, python=None):
        self.python = python
        self.process = None
        self.directory = None
        self.pending = False
        self.sequence = 0
        self.retired = []
        self.following = False
        self.latest = {}
        self.completed_request = None
        self.paused = False
        self.workspace_cache = None

    def reap(self):
        for process,directory in list(self.retired):
            if process is None or process.poll() is not None:
                if directory: shutil.rmtree(directory,ignore_errors=True)
                self.retired.remove((process,directory))

    def start(self):
        if self.process is not None and self.process.poll() is None: return
        if self.process is not None and self.process.stdin is not None:
            try: self.process.stdin.close()
            except OSError: pass
        self.pending=False
        self.following=False
        self.latest={}
        if self.directory: shutil.rmtree(self.directory,ignore_errors=True)
        self.directory = Path(tempfile.mkdtemp(prefix="remi-cloud-"))
        os.chmod(self.directory,0o700)
        try:
            self.process = subprocess.Popen([self.python or bundled_python(),"-I","-B","-u",str(Path(__file__).with_name("worker.py")),str(self.directory)],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                cwd=self.directory,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                env={key:value for key,value in os.environ.items() if key in {"PATH","SYSTEMROOT","WINDIR","TEMP","TMP","TMPDIR","LANG","LC_ALL","LC_CTYPE"}})
        except Exception:
            shutil.rmtree(self.directory,ignore_errors=True)
            self.directory = None
            raise

    def command(self, action, base_url=DEFAULT_API, **payload):
        url = api_url(base_url)
        if self.workspace_cache:payload.setdefault("workspace_cache",str(self.workspace_cache))
        self.start()
        if self.pending: raise RuntimeError("Wait for the current Cloud request or cancel sign-in.")
        self.paused=False
        self.pending = True
        self.sequence += 1
        # The pipe contains only a non-secret command. Session secrets remain in
        # the isolated process; neither scene properties nor files receive them.
        try:
            self.process.stdin.write((json.dumps({**payload,"action":action,"base_url":url,"request_id":self.sequence})+"\n").encode())
            self.process.stdin.flush()
        except (OSError,BrokenPipeError):
            self.pending = False
            raise RuntimeError("The Remi Cloud connection stopped. Sign in again.") from None

    def status(self):
        self.reap()
        if self.process is None or self.process.poll() is not None:
            self.pending = False
            self.following = False
            return {"state":"signed_out","message":"Stopped waiting locally. Cloud work may continue; resume the saved request." if self.paused else "Sign in to Remi Cloud."}
        try:
            path = self.directory/"status.json"
            if path.stat().st_size>262144: raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value,dict): raise ValueError
        except (OSError,ValueError):
            return {"state":"connecting","message":"Connecting to Remi Cloud…"}
        if value.get("request_id") != self.sequence:
            return {**self.latest,"state":"connecting","message":"Connecting to Remi Cloud…"}
        if value.get("state") in ("signed_in","signed_out","error","job_ready","job_failed"):
            self.pending = False
        self.latest = value
        if value.get("state") in ("job_ready","job_failed","error"):
            self.following = False
        if value.get("state") in ("job_ready","job_failed"):
            self.completed_request=value.get("request_path")
        return {**value,"message":"Stopped waiting locally. Cloud work may continue; resume the saved request."} if self.paused else value

    def workspace_status(self):
        if self.directory is None:return {}
        try:
            path=self.directory/"workspace.json"
            if path.stat().st_size>8*1024*1024:return {}
            value=json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):return {}

    def workspace_command(self,action,base_url=DEFAULT_API,**payload):
        profile=self.status()
        if not self.workspace_cache or profile.get("signed_in") is not True or not profile.get("user_id"):
            raise RuntimeError("Sign in to Remi Cloud before sharing board edits.")
        base_url=api_url(base_url)
        key=hashlib.sha256((base_url+"\n"+profile["user_id"]).encode()).hexdigest()
        root=Path(self.workspace_cache)/key/"outbox";root.mkdir(parents=True,exist_ok=True)
        command_id=f"{time.time_ns():020d}-{uuid.uuid4().hex}"
        value={**payload,"action":action,"base_url":base_url,"user_id":profile["user_id"],"id":command_id}
        path=root/(command_id+".json");temporary=path.with_suffix(".partial")
        temporary.write_text(json.dumps(value,allow_nan=False),encoding="utf-8");temporary.replace(path)
        return command_id

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.stdin.write(b'{"action":"close"}\n')
                self.process.stdin.flush()
                self.process.stdin.close()
            except (OSError,BrokenPipeError): pass
            # Do not block Blender waiting for a network revocation. The worker
            # closes on EOF; an in-progress login has a ten-minute deadline.
            if self.pending:
                self.process.terminate()
        process, directory = self.process,self.directory
        self.process = self.directory = None
        self.pending = False
        self.following = False
        self.latest = {}
        self.completed_request = None
        self.paused = False
        if process is not None or directory is not None:
            self.retired.append((process,directory))
        return process,directory

    def cleanup(self):
        process,directory = self.stop()
        for process,directory in list(self.retired):
            if process is not None:
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if directory: shutil.rmtree(directory,ignore_errors=True)
            self.retired.remove((process,directory))


runtime = CloudProcess()
atexit.register(runtime.cleanup)
