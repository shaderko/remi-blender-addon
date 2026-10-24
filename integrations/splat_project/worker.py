"""One-shot Gaussian-splat-to-texture projection in external Python.

The worker reads a pickle-free mesh exchange archive and the native Remi splat
archive, rasterizes the mesh in UV space, evaluates the Gaussian field at every
covered texel, and writes albedo/normal/confidence PNGs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from splat_project.projection import project, save_textures  # noqa: E402


def write_status(output, phase, **extra):
    temporary = output / "status.partial.json"
    try:
        temporary.write_text(json.dumps({"phase": phase, **extra}), encoding="utf-8")
        for attempt in range(20):
            try:
                temporary.replace(output / "status.json")
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    except OSError as exc:
        print(f"Progress update skipped: {exc}", flush=True)
    print(phase, flush=True)


def _load_mesh(path):
    with np.load(path, allow_pickle=False) as archive:
        for name in ("positions", "normals", "uvs"):
            if name not in archive:
                raise ValueError(f"Mesh archive is missing {name}")
        mesh = {
            "positions": np.asarray(archive["positions"], dtype=np.float32),
            "normals": np.asarray(archive["normals"], dtype=np.float32),
            "uvs": np.asarray(archive["uvs"], dtype=np.float32),
        }
    if mesh["positions"].ndim != 3 or mesh["positions"].shape[1:] != (3, 3):
        raise ValueError("Mesh positions must be a (triangles, 3, 3) array")
    if mesh["normals"].shape != mesh["positions"].shape:
        raise ValueError("Mesh normals must match positions")
    if mesh["uvs"].shape != (len(mesh["positions"]), 3, 2):
        raise ValueError("Mesh UVs must be a (triangles, 3, 2) array")
    if len(mesh["positions"]) == 0:
        raise ValueError("Mesh has no triangles")
    return mesh


def _load_splats(path):
    with np.load(path, allow_pickle=False) as archive:
        for name in ("positions", "scales", "rotations", "opacities", "sh_dc"):
            if name not in archive:
                raise ValueError(f"Gaussian archive is missing {name}")
        arrays = {
            "positions": np.asarray(archive["positions"], dtype=np.float32),
            "scales": np.asarray(archive["scales"], dtype=np.float32),
            "rotations": np.asarray(archive["rotations"], dtype=np.float32),
            "opacities": np.asarray(archive["opacities"], dtype=np.float32),
            "sh_dc": np.asarray(archive["sh_dc"], dtype=np.float32),
        }
    count = len(arrays["positions"])
    if not 0 < count <= 262144:
        raise ValueError(f"Unsupported Gaussian count: {count}")
    for name, value in arrays.items():
        if not np.isfinite(value).all():
            raise ValueError(f"Gaussian archive contains non-finite {name}")
    if np.any(arrays["scales"] <= 0):
        raise ValueError("Gaussian scales must be positive")
    if np.any((arrays["opacities"] < 0) | (arrays["opacities"] > 1)):
        raise ValueError("Gaussian opacity must be in [0, 1]")
    return arrays


def run(request, output):
    started = time.perf_counter()
    try:
        import PIL  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Texture projection requires Pillow in the external Python environment"
        ) from exc
    archive_path = Path(request["archive"])
    mesh_path = Path(request["mesh"])
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    if not mesh_path.is_file():
        raise FileNotFoundError(mesh_path)

    size = int(request.get("texture_size", 2048))
    samples = int(request.get("samples", 16))
    normal_source = str(request.get("normal_source", "AUTO")).upper()
    normal_strength = float(request.get("normal_strength", 1.0))
    normal_smoothing = int(request.get("normal_smoothing", 2))
    fill_holes = bool(request.get("fill_holes", True))
    fill_background = bool(request.get("fill_background", True))
    margin_px = int(request.get("margin_px", 8))

    write_status(output, "Loading mesh and Gaussians")
    mesh = _load_mesh(mesh_path)
    splats = _load_splats(archive_path)

    write_status(output, "Rasterizing texture")
    result = project(
        mesh,
        splats,
        texture_size=size,
        samples=samples,
        normal_source=normal_source,
        normal_strength=normal_strength,
        normal_smoothing=normal_smoothing,
        fill_holes=fill_holes,
        fill_background=fill_background,
        margin_px=margin_px,
    )

    write_status(output, "Saving textures")
    paths = save_textures(result, output)
    report = dict(result.report)
    report.update(
        {
            "schema": 1,
            "archive": str(archive_path),
            "mesh": str(mesh_path),
            "source": request.get("source", ""),
            "albedo": paths["albedo"],
            "normal": paths["normal"],
            "confidence": paths["confidence"],
            "device": "cpu",
            "elapsed_seconds": float(time.perf_counter() - started),
            "projector": "UV-space Gaussian field projection",
        }
    )
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "complete.json").write_text(
        json.dumps({"schema": 1, "complete": True}), encoding="utf-8",
    )
    write_status(
        output,
        "Complete",
        complete=True,
        covered_texels=report["covered_texels"],
    )
    print(json.dumps(report, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    output = args.request.resolve().parent
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        run(request, output)
    except Exception as exc:
        traceback.print_exc()
        write_status(output, "Failed", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
