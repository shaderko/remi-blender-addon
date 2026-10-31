"""Prepare a pinned Apache-2.0 DA3-Base snapshot for offline reconstruction.

Downloads source and safetensors only; never installs or executes upstream code.
The checkpoint digest is the upstream Hugging Face LFS SHA-256, not TOFU.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path, PurePosixPath
import urllib.request
import zipfile

from prepare_stereo import sha256


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPO = "ByteDance-Seed/Depth-Anything-3"
SOURCE_COMMIT = "3d835ec1a5802d64a8b8b15f817a1ab54809bfe4"
MODEL_REPO = "depth-anything/DA3-BASE"
MODEL_COMMIT = "f4a6c9b3c95e41c82048423d3493a81ec3fa810e"
WEIGHTS_SHA256 = "e01067dc1659613083d9145a9a2547ccdbe6ccbbf83c4fe7b3e8a4e2bdae78b5"
WEIGHTS_SIZE = 541518028


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Remi-multiview-research"})
    return urllib.request.urlopen(request, timeout=120)


def verify_bundle(output):
    output = Path(output)
    manifest = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    if (manifest["source_commit"], manifest["model_commit"]) != (SOURCE_COMMIT, MODEL_COMMIT):
        raise ValueError("Unexpected DA3 snapshot")
    required = {"source/src/depth_anything_3/api.py", "source/LICENSE", "model/config.json",
                "model/model.safetensors"}
    if not required.issubset(manifest["files"]):
        raise ValueError("Incomplete DA3 manifest")
    for name, record in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe snapshot path")
        target = output / name
        if not target.is_file() or sha256(target) != record["sha256"]:
            raise ValueError(f"Missing or modified DA3 file: {name}")
    if sha256(output / "model/model.safetensors") != WEIGHTS_SHA256:
        raise ValueError("DA3 checkpoint does not match upstream LFS digest")
    # Verify all importable files, not only the ones listed in the manifest.
    for path in (output / "source").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".json"}:
            if path.relative_to(output).as_posix() not in manifest["files"]:
                raise ValueError(f"Untracked code/config in DA3 snapshot: {path}")
    return manifest


def prepare(output):
    output = Path(output).resolve()
    if (output / "provenance.json").exists():
        verify_bundle(output)
        print(f"Verified DA3-Base: {output}", flush=True)
        return
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Untracked/partial bundle; use a fresh folder: {output}")
    output.mkdir(parents=True, exist_ok=True)
    records = {}

    def record(name, url):
        target = output / name
        records[name] = {"sha256": sha256(target), "bytes": target.stat().st_size, "url": url}

    url = f"https://codeload.github.com/{SOURCE_REPO}/zip/{SOURCE_COMMIT}"
    print("Downloading pinned DA3 source", flush=True)
    with download(url) as response:
        snapshot = response.read()
    with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
        for entry in archive.infolist():
            parts = PurePosixPath(entry.filename).parts[1:]
            if not parts or entry.is_dir() or ".." in parts:
                continue
            name = PurePosixPath(*parts).as_posix()
            if not (name.startswith("src/depth_anything_3/") or name in {"LICENSE", "README.md", "pyproject.toml"}):
                continue
            target = output / "source" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
            record("source/" + name, url)
    for name in ("config.json", "README.md", "model.safetensors"):
        url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_COMMIT}/{name}"
        target = output / "model" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading DA3-Base {name}", flush=True)
        temporary = target.with_suffix(target.suffix + ".download")
        with download(url) as response, temporary.open("xb") as stream:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                stream.write(chunk)
        if name == "model.safetensors":
            if temporary.stat().st_size != WEIGHTS_SIZE or sha256(temporary) != WEIGHTS_SHA256:
                raise ValueError("DA3 checkpoint failed upstream size/SHA-256 verification")
        temporary.rename(target)
        record("model/" + name, url)
    manifest = {"schema": 1, "source_repository": SOURCE_REPO, "source_commit": SOURCE_COMMIT,
                "model_repository": MODEL_REPO, "model_commit": MODEL_COMMIT,
                "license": "Apache-2.0 (source and DA3-Base weights)",
                "checkpoint_verification": "upstream Hugging Face LFS SHA-256",
                "files": records}
    (output / "provenance.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    verify_bundle(output)
    print(f"Prepared DA3-Base: {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "models/multiview/da3-base")
    prepare(parser.parse_args().output)
