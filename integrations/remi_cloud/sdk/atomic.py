"""Atomic publication that tolerates brief Windows reader/antivirus locks."""
import json
import os
from pathlib import Path
import tempfile
import time


def replace_file(source, destination, *, cancelled=lambda: False):
    for delay in (0.025, 0.05, 0.1, 0.2, 0.25, 0.25, 0.25, None):
        if cancelled():
            raise InterruptedError('Download cancelled')
        try:
            Path(source).replace(destination)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def write_json(path, value):
    path = Path(path)
    body = json.dumps(value, allow_nan=False).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.ipc-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
        replace_file(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
