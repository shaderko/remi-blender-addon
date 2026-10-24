"""Fuse calibrated learned-stereo depths; preserve raw geometry for comparison."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from splat_reconstruction.fusion import TSDF, color_vertices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--depths", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=256)
    parser.add_argument("--min-views", type=int, default=2)
    parser.add_argument("--min-weight", type=float, default=0.25)
    parser.add_argument("--truncation-voxels", type=float, default=4)
    args = parser.parse_args()
    dataset = json.loads((args.views / "dataset.json").read_text())
    depth_report = json.loads((args.depths / "depths.json").read_text())
    if Path(depth_report["dataset"]).resolve() != args.views.resolve():
        raise ValueError("Depth maps belong to a different rendered dataset")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"Output is not empty: {args.output}")
    started = time.perf_counter()
    volume = TSDF(dataset["bounds"], args.resolution, args.truncation_voxels)
    pairs = {pair["index"]: pair for pair in dataset["pairs"]}

    def observations():
        for record in depth_report["pairs"]:
            pair = pairs[record["index"]]
            with np.load(args.depths / record["file"], allow_pickle=False) as data:
                depth, confidence = data["depth"], data["confidence"]
            if depth.shape != tuple(reversed(dataset["resolution"])):
                raise ValueError("Depth shape does not match dataset intrinsics")
            with Image.open(args.views / pair["left"]) as image:
                rgb = np.asarray(image.convert("RGB"))
            yield depth, confidence, rgb, pair["left_camera_to_world_opencv"]

    for index, (depth, confidence, _, c2w) in enumerate(observations()):
        volume.integrate(depth, confidence, c2w, dataset["intrinsics"])
        print(f"Fused {index + 1}/{len(pairs)}", flush=True)
    vertices, faces = volume.extract(args.min_views, args.min_weight)
    colors, color_coverage = color_vertices(vertices, observations(), dataset["intrinsics"],
                                           volume.voxel * 2)
    import trimesh
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=colors, process=False)
    args.output.mkdir(parents=True, exist_ok=True)
    mesh.export(args.output / "mesh.ply")
    report = {"schema": 1, "method": "confidence-weighted learned-stereo TSDF",
              "dataset": str(args.views.resolve()), "depth_report": str((args.depths / 'depths.json').resolve()),
              "resolution": args.resolution, "grid_shape": volume.shape,
              "voxel_size": volume.voxel, "truncation": volume.truncation,
              "min_views": args.min_views, "min_weight": args.min_weight,
              "integrated_views": volume.integrated, "vertices": len(vertices), "triangles": len(faces),
              "colored_vertex_fraction": color_coverage, "seconds": time.perf_counter() - started,
              "postprocessing": "none; unknown-space crossings rejected; no hole fill/component deletion"}
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
