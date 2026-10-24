"""Save shipped-density and pre-cleanup ablations without changing production code."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import time


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=224)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    if not 96 <= args.resolution <= 384:
        raise ValueError("Baseline resolution must be between 96 and 384")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"Output is not empty: {args.output}")
    import numpy as np
    import torch
    import trimesh
    source = load_module("benchmark_splat_data", ROOT / "integrations/triposplat/splat_data.py")
    vendor = load_module("benchmark_density", ROOT / "integrations/splat_mesh/vendor/splat_mesh.py")
    data = source.load(args.archive)
    xyz, opacity, scale, quat = [torch.as_tensor(data[key], device=args.device) for key in
                                 ("positions", "opacities", "scales", "rotations")]
    rgb = torch.as_tensor(np.clip(data["sh_dc"] * 0.28209479177387814 + 0.5, 0, 1), device=args.device)
    args.output.mkdir(parents=True, exist_ok=True)
    reports = {}
    for method, min_component, taubin in (("density_pre_cleanup", 0, 0), ("density_shipped", 500, 12)):
        started = time.perf_counter()
        result = vendor.splat_to_mesh(
            xyz, opacity, scale, quat, rgb, resolution=args.resolution, device=torch.device(args.device),
            kernel=5, level_bias=0.4, min_component=min_component, min_opacity=0.02,
            color_sharpen=2.0, taubin=taubin, scale_floor_frac=0.9, vol_smooth=0.35, filaments=True)
        if result is None:
            raise RuntimeError(f"{method} produced no mesh")
        vertices, faces, colors = result
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces,
                               vertex_colors=np.rint(colors * 255).astype(np.uint8), process=False)
        mesh.export(args.output / f"{method}.ply")
        reports[method] = {"resolution": args.resolution, "min_component": min_component,
                           "taubin": taubin, "triangles": len(faces),
                           "seconds": time.perf_counter() - started,
                           "note": "Same density preprocessing and adaptive iso-level selection; only component cleanup/mesh smoothing differ"}
        print(f"{method}: {len(faces)} triangles", flush=True)
    (args.output / "report.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
