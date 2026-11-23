"""Isolated local image worker; no bpy, Remi server, or stored OAuth tokens."""
import base64
import binascii
import json
import hashlib
import stat
import os
from pathlib import Path
import struct
import sys
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from auth import AccessDenied, Connection, IMAGE_URL, request_json


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def png_dimensions(data):
    if not 45 <= len(data) <= 64 * 1024 * 1024 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Codex returned an invalid PNG image.")
    offset, width, height, image_data = 8, 0, 0, False
    while offset + 12 <= len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + size
        if end > len(data):
            break
        kind, payload = data[offset + 4:offset + 8], data[offset + 8:end - 4]
        checksum = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != checksum:
            break
        if offset == 8:
            if kind != b"IHDR" or size != 13:
                break
            width, height = struct.unpack_from(">II", payload)
            if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 32 * 1024 * 1024):
                break
        elif kind == b"IDAT":
            image_data = True
        elif kind == b"IEND":
            if size == 0 and end == len(data) and image_data:
                return width, height
            break
        offset = end
    raise ValueError("Codex returned an incomplete or invalid PNG image.")


MAX_REFERENCES = 16
MAX_REFERENCE_BYTES = 20 * 1024 * 1024


def prepare_references(directory, references):
    """Freeze validated ordered image bytes before any upstream request."""
    if not isinstance(references, list) or len(references) > MAX_REFERENCES:
        raise ValueError("Choose up to 16 context images.")
    inputs, metadata, total = [], [], 0
    for index, reference in enumerate(references):
        if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
            raise ValueError("Invalid context image. Select its card again.")
        path = Path(reference["path"])
        try:
            with path.open("rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_REFERENCE_BYTES:
                    raise ValueError("Context images must be nonempty files totaling at most 20 MiB.")
                if (reference.get("bytes", before.st_size) != before.st_size
                        or reference.get("mtime_ns", before.st_mtime_ns) != before.st_mtime_ns):
                    raise ValueError("A context image changed while queued. Select it and submit the prompt again.")
                data = source.read(MAX_REFERENCE_BYTES + 1)
                after = os.fstat(source.fileno())
        except OSError:
            raise ValueError("A queued context image is unavailable. Select it and submit the prompt again.") from None
        total += len(data)
        if (total > MAX_REFERENCE_BYTES or len(data) != before.st_size
                or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
            raise ValueError("Context images changed or exceed the 20 MiB total limit. Submit the prompt again.")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            png_dimensions(data)
            mime, suffix = "image/png", ".png"
        elif data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            mime, suffix = "image/jpeg", ".jpg"
        elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP" and int.from_bytes(data[4:8], "little") + 8 == len(data):
            mime, suffix = "image/webp", ".webp"
        else:
            raise ValueError("Context images must be valid PNG, JPEG or WebP files.")
        folder = directory / "references"
        folder.mkdir(exist_ok=True)
        snapshot = folder / f"{index:03d}{suffix}"
        snapshot.write_bytes(data)
        inputs.append({"image_url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")})
        metadata.append({"id": reference.get("id", ""), "file": str(snapshot.relative_to(directory)),
                         "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    return inputs, metadata


def generate(connection, request, progress):
    directory = Path(request["directory"])
    prompt = str(request["prompt"]).strip()
    count = int(request["count"])
    if not prompt or len(prompt) > 32000 or not 1 <= count <= 8:
        raise ValueError("Enter a prompt and choose between 1 and 8 images.")
    progress("Preparing image context…")
    input_images, references = prepare_references(directory, request.get("references", []))
    images = []
    meta = {"prompt": prompt, "negative_prompt": "", "model": "codex",
            "model_label": "Codex · GPT Image", "seed": 0, "images": images,
            "quality": request.get("quality", "low"), "requested_size": request.get("size", "1024x1024"),
            "references": references}
    for index in range(count):
        headers = connection.authorize(progress)
        progress(f"Generating image {index + 1}/{count} with Codex…")
        payload = {"model": "gpt-image-2", "prompt": prompt, "n": 1,
                   "quality": request.get("quality", "low"), "size": request.get("size", "1024x1024"),
                   "background": "opaque"}
        endpoint = IMAGE_URL
        if input_images:
            endpoint = IMAGE_URL.rsplit("/", 1)[0] + "/edits"
            payload["images"] = input_images
        result = request_json(endpoint, headers={**headers, "originator": "remi-local-codex-images", "x-codex-image-turn-id": str(uuid.uuid4())}, data=payload)
        values = result.get("data")
        if not isinstance(values, list) or not values or not isinstance(values[0], dict):
            raise ValueError("Codex returned no image.")
        encoded = values[0].get("b64_json")
        if not isinstance(encoded, str):
            raise ValueError("Codex returned no image data.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Codex returned invalid image data.") from None
        width, height = png_dimensions(data)
        name = f"image_{index:03d}.png"
        temporary = directory / (name + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(directory / name)
        images.append({"file": name, "seed": 0, "width": width, "height": height})
        meta.update(width=width, height=height)
        write_json(directory / "meta.json", meta)
    write_json(directory / "complete.json", {"schema": 1, "complete": True})


def main():
    connection = Connection()
    for line in sys.stdin:
        request = json.loads(line)
        directory = Path(request["directory"])
        def progress(message):
            write_json(directory / "status.json", {"phase": message})
        try:
            if request["action"] == "sign_in":
                connection.sign_in(progress)
            elif request["action"] == "generate":
                generate(connection, request, progress)
            else:
                raise ValueError("Unknown local Codex command.")
            write_json(directory / "status.json", {"phase": "Complete", "done": True, "signed_in": bool(connection.tokens)})
        except Exception as error:
            if isinstance(error, AccessDenied):
                connection.tokens = None
            # Only our bounded errors reach disk. Other exceptions may include
            # request headers or OAuth callback details in their representation.
            message = str(error) if isinstance(error, (RuntimeError, ValueError)) and not isinstance(error, json.JSONDecodeError) else "The local Codex connection failed. Check your connection and sign in again."
            write_json(directory / "status.json", {"phase": "Codex image request failed", "done": True, "error": message[:400], "signed_in": bool(connection.tokens)})


if __name__ == "__main__":
    main()
