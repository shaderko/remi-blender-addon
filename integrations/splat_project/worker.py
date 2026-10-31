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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.splat_project.projection import (  # noqa: E402
    project,
    project_from_view,
    save_textures,
    save_view_layer,
    save_view_normal,
    unblend_view,
)


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
        for name in ("tangents", "tangent_signs"):
            if name in archive:
                mesh[name] = np.asarray(archive[name], dtype=np.float32)
        if "vertex_indices" in archive:
            mesh["vertex_indices"] = np.asarray(archive["vertex_indices"], dtype=np.int32)
        if "orientation_sign" in archive:
            mesh["orientation_sign"] = np.asarray(archive["orientation_sign"], dtype=np.float32)
    if mesh["positions"].ndim != 3 or mesh["positions"].shape[1:] != (3, 3):
        raise ValueError("Mesh positions must be a (triangles, 3, 3) array")
    if mesh["normals"].shape != mesh["positions"].shape:
        raise ValueError("Mesh normals must match positions")
    if mesh["uvs"].shape != (len(mesh["positions"]), 3, 2):
        raise ValueError("Mesh UVs must be a (triangles, 3, 2) array")
    if len(mesh["positions"]) == 0:
        raise ValueError("Mesh has no triangles")
    for name, value in mesh.items():
        if not np.isfinite(value).all():
            raise ValueError(f"Mesh contains non-finite {name}")
    if "tangents" in mesh and mesh["tangents"].shape != mesh["positions"].shape:
        raise ValueError("Mesh tangents must match positions")
    if "tangent_signs" in mesh and mesh["tangent_signs"].shape != mesh["positions"].shape[:2]:
        raise ValueError("Mesh tangent signs must match triangle corners")
    if "vertex_indices" in mesh and (mesh["vertex_indices"].shape != mesh["positions"].shape[:2]
                                      or np.any(mesh["vertex_indices"] < 0)):
        raise ValueError("Mesh vertex indices must match triangle corners")
    if "orientation_sign" in mesh and (mesh["orientation_sign"].shape != ()
                                        or float(mesh["orientation_sign"]) not in {-1.0, 1.0}):
        raise ValueError("Mesh orientation sign must be +1 or -1")
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
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Texture projection requires Pillow in the external Python environment"
        ) from exc
    mesh_path = Path(request["mesh"])
    if not mesh_path.is_file():
        raise FileNotFoundError(mesh_path)

    mode = str(request.get("mode", "field"))
    if mode not in {"field", "octree", "octree_local", "view"}:
        raise ValueError("Unknown projection mode")
    size = int(request.get("texture_size", 2048))

    write_status(output, "Loading mesh")
    mesh = _load_mesh(mesh_path)

    if mode == "view":
        view_path = Path(request["view_image"])
        if not view_path.is_file():
            raise FileNotFoundError(view_path)
        background = request.get("view_background")
        write_status(output, "Loading view")
        if background:
            background_path = Path(background)
            if not background_path.is_file():
                raise FileNotFoundError(background_path)
            with Image.open(view_path) as image:
                black = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
            with Image.open(background_path) as image:
                white = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
            view_image = unblend_view(black, white)
        else:
            with Image.open(view_path) as image:
                view_image = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
        normal_values = None
        normal_path = request.get("normal_image")
        if normal_path:
            normal_path = Path(normal_path)
            normal_background = request.get("normal_background")
            if not normal_path.is_file():
                raise FileNotFoundError(normal_path)
            if normal_background:
                normal_background = Path(normal_background)
                if not normal_background.is_file():
                    raise FileNotFoundError(normal_background)
                with Image.open(normal_path) as image:
                    black = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
                with Image.open(normal_background) as image:
                    white = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
                normal_values = unblend_view(black, white)
            else:
                with Image.open(normal_path) as image:
                    normal_values = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
        write_status(output, "Projecting view")
        result = project_from_view(
            mesh,
            view_image,
            request["view_matrix"],
            request["projection_matrix"],
            texture_size=size,
            alpha_threshold=float(request.get("alpha_threshold", 0.01)),
            depth_epsilon=float(request.get("depth_epsilon", 1e-4)),
            view_mask=request.get("view_mask"),
            view_mask_feather=float(request.get("view_mask_feather", 24.0)),
            normal_image=normal_values,
            normal_strength=float(request.get("normal_strength", 1.0)),
            normal_smoothing=int(request.get("normal_smoothing", 2)),
        )
        write_status(output, "Saving layer")
        layer_path = save_view_layer(result, output)
        normal_output = save_view_normal(result, output)
        report = dict(result.report)
        report.update(
            {
                "schema": 1,
                "mesh": str(mesh_path),
                "source": request.get("source", ""),
                "view_image": str(view_path),
                "layer": layer_path,
                "normal": normal_output,
                "device": "cpu",
                "elapsed_seconds": float(time.perf_counter() - started),
                "projector": "viewport camera view projection",
            }
        )
    else:
        archive_path = Path(request["archive"])
        if not archive_path.is_file():
            raise FileNotFoundError(archive_path)
        samples = int(request.get("samples", 16))
        normal_source = str(request.get("normal_source", "AUTO")).upper()
        normal_strength = float(request.get("normal_strength", 1.0))
        normal_smoothing = int(request.get("normal_smoothing", 2))
        fill_holes = bool(request.get("fill_holes", True))
        fill_background = bool(request.get("fill_background", True))
        margin_px = int(request.get("margin_px", 8))

        write_status(output, "Loading Gaussians")
        splats = _load_splats(archive_path)

        write_status(output, "Rasterizing texture")
        options = dict(texture_size=size, normal_strength=normal_strength,
                       normal_smoothing=normal_smoothing, fill_holes=fill_holes,
                       fill_background=fill_background, margin_px=margin_px)
        if mode in {"octree", "octree_local"}:
            from integrations.splat_project.octree import project_octree

            def cancelled():
                if (output / "cancel.request").exists():
                    raise InterruptedError("Texture projection cancelled")

            if mode == "octree_local":
                from integrations.splat_project.local_gaussian import project_local
                result = project_local(
                    mesh, splats, match_distance=float(request.get("match_distance", 0.02)),
                    layer_width=float(request.get("layer_width", 0.005)),
                    normal_mode=request.get("local_normal_mode", "AUTO"),
                    smooth_rays=bool(request.get("smooth_rays", True)),
                    progress=lambda phase: write_status(output, phase), cancelled=cancelled, **options)
            else:
                result = project_octree(
                    mesh, splats, ray_distance=float(request.get("ray_distance", 0.1)),
                    density=float(request.get("surface_density", 0.5)),
                    max_steps=int(request.get("ray_steps", 512)),
                    progress=lambda phase: write_status(output, phase), cancelled=cancelled, **options)
        else:
            result = project(mesh, splats, samples=samples, normal_source=normal_source, **options)

        write_status(output, "Saving textures")
        if (output / "cancel.request").exists():
            raise InterruptedError("Texture projection cancelled")
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
                "projector": ("Source-local Gaussian alpha compositing" if mode == "octree_local" else
                              "Octree density-surface ray bake (legacy)" if mode == "octree" else
                              "UV-space Gaussian field projection"),
            }
        )
    if (output / "cancel.request").exists():
        raise InterruptedError("Texture projection cancelled")
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
