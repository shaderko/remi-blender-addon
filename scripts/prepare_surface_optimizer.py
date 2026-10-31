"""Fetch the pinned external Windows Spirula research tool; do not install it.

GPL-3.0 tool, NOT bundled with Remi. Verify the official GitHub asset digest
before extraction. No administrator privileges or system configuration changes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import urllib.request
import zipfile

from prepare_stereo import sha256


ROOT = Path(__file__).resolve().parents[1]
TAG = "v2026.10.16"
NAME = "spirula-2026.10.16-windows-vulkan-x86_64.zip"
URL = f"https://github.com/harry7557558/spirula-studio/releases/download/{TAG}/{NAME}"
DIGEST = "9b83ba6d78a4ace562d2ad2f964d2e3eb15aaa7a78918eab54e44d8a98a649d0"
SIZE = 49168988


def prepare(output):
    output = Path(output).resolve()
    if (output / "provenance.json").exists():
        manifest = json.loads((output / "provenance.json").read_text())
        if manifest["asset_sha256"] != DIGEST:
            raise ValueError("Unexpected external-tool snapshot")
        for name, digest in manifest["files"].items():
            path = (output / name).resolve()
            if not path.is_relative_to(output) or sha256(path) != digest:
                raise ValueError(f"Modified tool: {name}")
        print(f"Verified {output}", flush=True)
        return
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Use a fresh tool folder: {output}")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / NAME
    request = urllib.request.Request(URL, headers={"User-Agent": "Remi-surface-research"})
    print(f"Downloading external Spirula {TAG} (49 MB)", flush=True)
    with urllib.request.urlopen(request, timeout=120) as response, archive.open("xb") as stream:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            stream.write(chunk)
    if archive.stat().st_size != SIZE or sha256(archive) != DIGEST:
        raise ValueError("External tool failed official size/SHA-256 verification")
    files = {}
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename:
                raise ValueError("Unsafe external-tool archive path")
            if info.is_dir():
                continue
            path = output / "tool" / info.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bundle.read(info))
            files[path.relative_to(output).as_posix()] = sha256(path)
    (output / "provenance.json").write_text(json.dumps({
        "repository": "harry7557558/spirula-studio", "tag": TAG, "url": URL,
        "asset_sha256": DIGEST, "verification": "official GitHub release asset digest",
        "license": "GPL-3.0; retain upstream notices; not bundled with Remi", "files": files,
    }, indent=2), encoding="utf-8")
    print(f"Prepared {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "models/surface/spirula-2026.10.16")
    prepare(parser.parse_args().output)
