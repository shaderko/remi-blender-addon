"""Public HTTPS model downloads. Standard library only; no Blender or torch."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import uuid

DEFAULT_MODEL_BASE = (
    "https://remipublic.hel1.your-objectstorage.com/models/"
    "triposplat-rand6_100_ncm-step1500-af36bcb6-v1/blender/"
)

if __package__:
    from .worker import COMPONENTS, status
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from worker import COMPONENTS, status


def model_base(value):
    url = urllib.parse.urlsplit(value.strip())
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or "\\" in value):
        raise ValueError("Model Download URL must be a public HTTPS directory URL")
    return urllib.parse.urlunsplit(url).rstrip("/") + "/"


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        model_base(new_url)  # Do not downgrade HTTPS or forward to credentialed URLs.
        return super().redirect_request(request, fp, code, message, headers, new_url)


def open_url(url):
    return urllib.request.build_opener(HTTPSRedirectHandler()).open(url, timeout=60)


def validate_manifest(manifest):
    if (not isinstance(manifest, dict) or manifest.get("schema") != 1
            or manifest.get("model") != "rand6_100_ncm"
            or not isinstance(manifest.get("files"), dict)
            or set(manifest["files"]) != set(COMPONENTS)):
        raise ValueError("Unsupported Blender model bundle")
    for name, item in manifest["files"].items():
        if (not isinstance(item, dict) or type(item.get("bytes")) is not int
                or item["bytes"] <= 0
                or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))):
            raise ValueError(f"Invalid model metadata: {name}")
    return manifest


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def destination(root, name):
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Model path escapes the selected folder: {name}")
    return path


def partial_path(root, name, token):
    path = destination(root, name)
    return path.with_name(f".{path.name}.remi-{token}.partial")


def install_verified(temporary, path, digest):
    """Publish without ever replacing a file created by another process."""
    try:
        if os.name == "nt":
            temporary.rename(path)  # Windows rename is atomic and refuses an existing target.
        else:
            os.link(temporary, path)  # POSIX rename would overwrite; link is no-clobber.
    except FileExistsError:
        if not path.is_file() or checksum(path) != digest:
            raise ValueError(f"Refusing to replace existing model: {path.name}")


def download_chunks(url, expected_bytes, opener):
    """Resume interrupted HTTP responses; never accept an unchecked range."""
    received = 0
    for attempt in range(3):
        request = url if not received else urllib.request.Request(
            f"{url}?remiOffset={received}", headers={"Range": f"bytes={received}-"},
        )
        try:
            with opener(request) as response:
                if received:
                    expected = f"bytes {received}-{expected_bytes - 1}/{expected_bytes}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected:
                        raise ValueError("Storage returned an invalid resumed download range")
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    yield chunk
                if received >= expected_bytes:
                    return
                # A declared but missing tail is a transport interruption, not a
                # reason to discard a verified prefix and restart a GB-sized file.
                if not getattr(response, "headers", {}).get("Content-Length"):
                    return
        except (OSError, http.client.IncompleteRead):
            if not received or attempt == 2:
                raise
    raise OSError("Model transfer was interrupted repeatedly; retry Download Models")


def download_models(base, models, report=lambda phase: None, *, token=None, opener=open_url):
    base = model_base(base)
    models = Path(models).resolve()
    token = token or uuid.uuid4().hex
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("Invalid download token")
    report("Reading model manifest…")
    with opener(base + "bundle.json") as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Model manifest is too large")
    manifest = validate_manifest(json.loads(raw))
    files = manifest["files"]
    # Validate all existing files before writing anything. Never replace user weights.
    missing = []
    for name, item in files.items():
        path = destination(models, name)
        if path.exists():
            report(f"Verifying {Path(name).name}…")
            if not path.is_file() or path.stat().st_size != item["bytes"] or checksum(path) != item["sha256"]:
                raise ValueError(f"Existing model differs; choose another Model Folder: {name}")
        else:
            missing.append(name)
    total = sum(files[name]["bytes"] for name in missing)
    received = 0
    last_percent = -1
    for name in missing:
        item = files[name]
        path = destination(models, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = partial_path(models, name, token)
        digest = hashlib.sha256()
        loaded = 0
        report(f"Downloading models · {received / max(total, 1):.0%}")
        try:
            with temporary.open("xb") as stream:
                for chunk in download_chunks(base + name, item["bytes"], opener):
                    loaded += len(chunk)
                    if loaded > item["bytes"]:
                        raise ValueError(f"Size mismatch: {name}")
                    digest.update(chunk)
                    stream.write(chunk)
                    received += len(chunk)
                    percent = int(received * 100 / max(total, 1))
                    if percent != last_percent:
                        report(f"Downloading models · {percent}%")
                        last_percent = percent
            if loaded != item["bytes"] or digest.hexdigest() != item["sha256"]:
                raise ValueError(f"Download size/checksum mismatch: {name}")
            install_verified(temporary, path, item["sha256"])
        finally:
            temporary.unlink(missing_ok=True)
    report("Models downloaded and verified")
    return models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    try:
        download_models(args.base_url, args.models,
                        lambda phase: status(args.status_dir, phase), token=args.token)
        (args.status_dir / "complete.json").write_text(
            json.dumps({"schema": 1, "complete": True}), encoding="utf-8")
    except Exception as exc:
        status(args.status_dir, "Model download failed", error=str(exc))
        raise


if __name__ == "__main__":
    main()
