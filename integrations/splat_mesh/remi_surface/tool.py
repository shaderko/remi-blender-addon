"""Explicit, checksum-pinned external surface-tool installation (stdlib only)."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import platform
import urllib.parse
import urllib.request
import uuid
import zipfile

from .common import sha256, status, write_json

TAG = "v2026.10.16"
NAME = "spirula-2026.10.16-windows-vulkan-x86_64.zip"
URL = f"https://github.com/harry7557558/spirula-studio/releases/download/{TAG}/{NAME}"
ARCHIVE_SHA256 = "9b83ba6d78a4ace562d2ad2f964d2e3eb15aaa7a78918eab54e44d8a98a649d0"
ARCHIVE_SIZE = 49168988
EXECUTABLE_SHA256 = "eb57050c90fc046f079d2b788eb62f0cf9639afbe50941ecb4f945d52876bd14"
FOLDER_NAME = "spirula-2026.10.16"


def platform_error():
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        return "Create Mesh currently requires Windows x64 with a Vulkan-capable GPU"
    return ""


def executable_path(folder):
    return Path(folder).resolve() / "tool" / "spirula.exe"


def verify_tool(folder):
    error = platform_error()
    if error:
        raise ValueError(error)
    executable = executable_path(folder)
    if not executable.is_file():
        raise ValueError("Surface tool is missing. Use Install Surface Tool in Generation Settings")
    if sha256(executable) != EXECUTABLE_SHA256:
        raise ValueError("Surface tool checksum differs from the supported release; choose a new Surface Tool Folder")
    return executable


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Refusing an unsafe surface-tool download redirect")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def install_tool(folder, progress=lambda phase: None):
    error = platform_error()
    if error:
        raise ValueError(error)
    folder = Path(folder).resolve()
    executable = executable_path(folder)
    if executable.exists():
        progress("Verifying surface tool…")
        return verify_tool(folder)
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / NAME
    if archive.exists():
        if not archive.is_file() or archive.stat().st_size != ARCHIVE_SIZE or sha256(archive) != ARCHIVE_SHA256:
            raise ValueError("Existing tool archive differs; choose a new Surface Tool Folder")
    else:
        temporary = folder / (NAME + "." + uuid.uuid4().hex + ".partial")
        request = urllib.request.Request(URL, headers={"User-Agent": "Remi-surface-installer"})
        opener = urllib.request.build_opener(HTTPSRedirectHandler())
        received, last_percent = 0, -1
        try:
            with opener.open(request, timeout=60) as response, temporary.open("xb") as stream:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    received += len(chunk)
                    if received > ARCHIVE_SIZE:
                        raise ValueError("Surface-tool download exceeds the expected size")
                    stream.write(chunk)
                    percent = int(received * 100 / ARCHIVE_SIZE)
                    if percent != last_percent:
                        progress(f"Downloading surface tool · {percent}%")
                        last_percent = percent
            if received != ARCHIVE_SIZE or sha256(temporary) != ARCHIVE_SHA256:
                raise ValueError("Surface-tool download failed SHA-256 verification")
            # Windows rename is atomic and never replaces another installer's file.
            try:
                temporary.rename(archive)
            except FileExistsError:
                if sha256(archive) != ARCHIVE_SHA256:
                    raise ValueError("Another surface-tool archive already exists")
        finally:
            temporary.unlink(missing_ok=True)
    progress("Verifying surface tool…")
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename:
                raise ValueError("Unsafe path in surface-tool archive")
        info = bundle.getinfo("spirula.exe")
        if info.file_size > 256 * 1024 * 1024:
            raise ValueError("Surface-tool executable is too large")
        executable.parent.mkdir(exist_ok=True)
        temporary = executable.with_name("spirula." + uuid.uuid4().hex + ".partial")
        try:
            with bundle.open(info) as source, temporary.open("xb") as destination:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    destination.write(chunk)
            if sha256(temporary) != EXECUTABLE_SHA256:
                raise ValueError("Surface-tool executable failed SHA-256 verification")
            try:
                temporary.rename(executable)
            except FileExistsError:
                verify_tool(folder)
        finally:
            temporary.unlink(missing_ok=True)
    write_json(folder / "provenance.json", {
        "repository": "harry7557558/spirula-studio", "tag": TAG, "url": URL,
        "asset_sha256": ARCHIVE_SHA256, "files": {"tool/spirula.exe": EXECUTABLE_SHA256},
        "license": "GPL-3.0; external optional tool, not bundled with Remi",
        "corresponding_source": f"https://github.com/harry7557558/spirula-studio/tree/{TAG}",
    })
    return verify_tool(folder)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        executable = install_tool(args.folder, lambda phase: status(args.status_dir, phase))
        write_json(args.status_dir / "complete.json", {"schema": 1, "complete": True, "executable": str(executable)})
        status(args.status_dir, "Surface tool installed and verified")
    except Exception as exc:
        status(args.status_dir, "Surface-tool installation failed", error=str(exc))
        raise
