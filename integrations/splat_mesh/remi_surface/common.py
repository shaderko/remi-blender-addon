"""Small stdlib-only exchange and progress helpers."""
import hashlib
import json
import time
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def status(output, phase, **extra):
    write_json(Path(output) / "status.json", {"phase": phase, **extra})


def check_cancel(output):
    if (Path(output) / "cancel.request").exists():
        raise InterruptedError("Mesh reconstruction cancelled")
