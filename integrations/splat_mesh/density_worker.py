"""Legacy density baseline for offline comparisons; not used by Create Mesh.

The mesher is a vendored, MIT-licensed port of Modly's SplatToMesh adapter:
anisotropic density rasterization + Surface Nets + Taubin smoothing + co-splatted
vertex colors. It needs torch and scipy, so it must not run inside Blender.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import traceback

import numpy as np


VENDOR = Path(__file__).resolve().parent / "vendor"
C0 = 0.28209479177387814


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


def run(request, output):
    started = time.perf_counter()
    archive = Path(request["archive"])
    if not archive.is_file():
        raise FileNotFoundError(archive)
    if str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))
    import torch
    import trimesh
    from splat_mesh import splat_to_mesh

    data = np.load(archive, allow_pickle=False)
    for name in ("positions", "scales", "rotations", "opacities", "sh_dc"):
        if name not in data:
            raise ValueError(f"Gaussian archive is missing {name}")
    count = len(data["positions"])
    if not 0 < count <= 262144:
        raise ValueError(f"Unsupported Gaussian count: {count}")
    device_name = request.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda is unavailable")
    device = torch.device(device_name)
    write_status(output, "Preparing Gaussians")
    xyz = torch.as_tensor(data["positions"], dtype=torch.float32, device=device)
    opacity = torch.as_tensor(data["opacities"], dtype=torch.float32, device=device)
    scale = torch.as_tensor(data["scales"], dtype=torch.float32, device=device)
    quat = torch.as_tensor(data["rotations"], dtype=torch.float32, device=device)
    rgb = torch.as_tensor(
        np.clip(data["sh_dc"].astype(np.float32) * C0 + 0.5, 0.0, 1.0),
        dtype=torch.float32, device=device,
    )
    resolution = int(request.get("resolution", 224))
    if not 96 <= resolution <= 384:
        raise ValueError("Mesh resolution must be between 96 and 384")
    write_status(output, "Building density surface")
    result = splat_to_mesh(
        xyz, opacity, scale, quat, rgb,
        resolution=resolution,
        device=device,
        kernel=5,
        level_bias=0.4,
        min_component=int(request.get("min_component", 500)),
        min_opacity=0.02,
        color_sharpen=2.0,
        taubin=int(request.get("taubin", 12)),
        scale_floor_frac=float(request.get("scale_floor", 0.9)),
        vol_smooth=float(request.get("vol_smooth", 0.35)),
        filaments=True,
    )
    if result is None:
        raise RuntimeError("Mesher produced no surface")
    vertices, faces, colors = result
    write_status(output, "Saving mesh")
    mesh = trimesh.Trimesh(
        vertices=vertices,
        faces=faces,
        vertex_colors=np.concatenate([
            np.clip(colors * 255.0, 0, 255).astype(np.uint8),
            np.full((len(colors), 1), 255, dtype=np.uint8),
        ], axis=1),
        process=False,
    )
    mesh_path = output / "mesh.ply"
    mesh.export(mesh_path)
    elapsed = time.perf_counter() - started
    report = {
        "schema": 1,
        "source": request.get("source", ""),
        "archive": str(archive),
        "mesh": str(mesh_path),
        "gaussians": count,
        "vertices": int(len(vertices)),
        "triangles": int(len(faces)),
        "resolution": resolution,
        "taubin": int(request.get("taubin", 12)),
        "device": str(device),
        "elapsed_seconds": elapsed,
        "mesher": "Modly SplatToMesh (anisotropic density + Surface Nets)",
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "complete.json").write_text(
        json.dumps({"schema": 1, "complete": True}), encoding="utf-8",
    )
    write_status(output, "Complete", complete=True, vertices=len(vertices))
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
