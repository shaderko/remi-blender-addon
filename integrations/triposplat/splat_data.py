"""Versioned, pickle-free Gaussian exchange format shared with Blender.

Positions and WXYZ rotations use the exported PLY's Z-up frame. Scales are
activated standard deviations, opacity is linear, and SH DC is NOT clipped RGB.
"""
from pathlib import Path
import zipfile
import struct

import numpy as np


SCHEMA = 1
MAX_SPLATS = 262144
WIDTHS = {"positions": 3, "scales": 3, "rotations": 4, "opacities": 0, "sh_dc": 3}


def validate(arrays):
    count = len(arrays["positions"])
    if not 0 < count <= MAX_SPLATS:
        raise ValueError(f"Expected 1–{MAX_SPLATS} Gaussians, got {count}")
    result = {}
    for name, width in WIDTHS.items():
        value = np.asarray(arrays[name], dtype=np.float32)
        shape = (count, width) if width else (count,)
        if value.shape != shape or not np.isfinite(value).all():
            raise ValueError(f"Invalid Gaussian array: {name}")
        result[name] = value
    if np.any(result["scales"] <= 0):
        raise ValueError("Gaussian scales must be positive")
    if np.any((result["opacities"] < 0) | (result["opacities"] > 1)):
        raise ValueError("Gaussian opacity must be in [0, 1]")
    norms = np.linalg.norm(result["rotations"], axis=1)
    if not np.allclose(norms, 1, atol=1e-3):
        raise ValueError("Gaussian rotations must be unit WXYZ quaternions")
    return result


def save(path, arrays):
    arrays = validate(arrays)
    # Uncompressed: small enough, fast to load, no pickle or precision loss.
    with Path(path).open("wb") as stream:
        np.savez(stream, schema=np.array(SCHEMA, dtype=np.int32), **arrays)


def load(path):
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(16)
        if header[:8] == b"REMIGAUS":
            if len(header) != 16:
                raise ValueError("Incomplete REMIGAUS header")
            version, count = struct.unpack_from("<II", header, 8)
            if version != SCHEMA or not 1 <= count <= MAX_SPLATS:
                raise ValueError("Unsupported REMIGAUS schema or count")
            expected = 16 + count * 56
            if path.stat().st_size != expected:
                raise ValueError("REMIGAUS byte length does not match its count")
            raw = stream.read(count * 56 + 1)
            if len(raw) != count * 56:
                raise ValueError("Incomplete REMIGAUS payload")
            values = np.frombuffer(raw, dtype="<f4").reshape(count, 14)
            return validate({"positions":values[:,0:3], "scales":values[:,3:6],
                             "rotations":values[:,6:10], "opacities":values[:,10], "sh_dc":values[:,11:14]})
    if path.suffix.lower() == ".splat":
        raise ValueError("Expected native Remi REMIGAUS data, not a viewer .splat file")
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024:
            raise ValueError("Gaussian archive exceeds the supported size")
    with np.load(path, allow_pickle=False) as archive:
        if int(archive["schema"]) != SCHEMA:
            raise ValueError("Unsupported Gaussian archive schema")
        return validate({name: archive[name] for name in WIDTHS})
