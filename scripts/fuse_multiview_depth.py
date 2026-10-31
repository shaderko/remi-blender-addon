"""Dense, un-decimated multiview surface fusion with an explicit raw ablation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np

from prepare_stereo import sha256
from splat_reconstruction.fusion import TSDF, color_vertices
from splat_reconstruction.metrics import topology_metrics
from splat_reconstruction.multiview import consistent_depth, nearest_context, training_cameras


def load_frames(views, depths):
    views, depths = Path(views).resolve(), Path(depths).resolve()
    dataset = json.loads((views / "dataset.json").read_text(encoding="utf-8"))
    report = json.loads((depths / "depths.json").read_text(encoding="utf-8"))
    if (Path(report["dataset"]).resolve() != views or report["dataset_sha256"] != sha256(views / "dataset.json")
            or report["source_sha256"] != dataset["source_sha256"]
            or sha256(dataset["source_archive"]) != dataset["source_sha256"]):
        raise ValueError("Depths/source do not match the frozen rendered dataset")
    cameras = training_cameras(dataset)
    records = {record["index"]: record for record in report["frames"]}
    if len(records) != len(report["frames"]) or set(records) != {c["index"] for c in cameras}:
        raise ValueError("Depth frames must cover every training camera exactly once")
    resolution = report["resolution"]
    # Match the same half-pixel resize convention independently of the network.
    w, h = dataset["resolution"]
    if w != h:
        raise ValueError("Expected square renders")
    expected_k = np.asarray(dataset["intrinsics"], dtype=float).copy()
    expected_k[0, 0] *= resolution / w
    expected_k[1, 1] *= resolution / h
    expected_k[0, 2] = (expected_k[0, 2] + 0.5) * resolution / w - 0.5
    expected_k[1, 2] = (expected_k[1, 2] + 0.5) * resolution / h - 0.5
    frames = []
    for camera in cameras:
        record = records[camera["index"]]
        path = (depths / record["file"]).resolve()
        if not path.is_relative_to(depths) or record["rgb"] != camera["rgb"]:
            raise ValueError("Unexpected depth file or training image")
        with np.load(path, allow_pickle=False) as data:
            frame = {name: data[name].copy() for name in ("depth", "confidence", "rgb", "intrinsics", "camera")}
        if (frame["depth"].shape != (resolution, resolution)
                or frame["confidence"].shape != frame["depth"].shape
                or frame["rgb"].shape != (resolution, resolution, 3)
                or not np.allclose(frame["camera"], camera["camera"], atol=1e-6)
                or not np.allclose(frame["intrinsics"], expected_k, atol=1e-5)):
            raise ValueError("Depth maps/calibration mismatch")
        for key in ("depth", "confidence"):
            if not np.isfinite(frame[key]).all() or np.any(frame[key] < 0):
                raise ValueError("Depth/confidence must be finite and nonnegative")
        if np.any(frame["confidence"] > 1):
            raise ValueError("Fusion weights must be <=1")
        frames.append(frame)
    return dataset, report, cameras, frames


def fuse(views, depths, output, resolution=512, raw=False, min_support=1,
         relative_tolerance=0.003, pixel_tolerance=1.5, neighbors=12,
         min_views=2, truncation_voxels=4.0, surface_band_only=False):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    dataset, _, cameras, frames = load_frames(views, depths)
    if neighbors < 2:
        raise ValueError("Need at least two neighboring cameras")
    started = time.perf_counter()
    volume = TSDF(dataset["bounds"], resolution, truncation_voxels)
    filtered, coverage = [], []
    for index, frame in enumerate(frames):
        if raw:
            depth, weight = frame["depth"], frame["confidence"]
        else:
            selected = nearest_context(cameras, index, min(neighbors + 1, len(cameras)))[1:]
            depth, weight, _ = consistent_depth(
                frame, [frames[i] for i in selected], min_support=min_support,
                relative_tolerance=relative_tolerance, pixel_tolerance=pixel_tolerance)
        filtered.append({**frame, "depth": depth, "confidence": weight})
        before, after = int((frame["depth"] > 0).sum()), int((depth > 0).sum())
        coverage.append({"index": cameras[index]["index"], "before": before, "after": after,
                         "retention": after / max(before, 1)})
        volume.integrate(depth, weight, frame["camera"], frame["intrinsics"], surface_band_only=surface_band_only)
        print(f"Fused {index + 1}/{len(frames)}: {after:,}/{before:,} pixels", flush=True)
    vertices, faces = volume.extract(min_views=min_views, min_weight=0.25)
    # Every frame has the same processed K (checked at load time), but never use
    # the original render K on resized depth maps.
    observations = [(f["depth"], f["confidence"], f["rgb"], f["camera"]) for f in filtered]
    colors, colored = color_vertices(vertices, observations, frames[0]["intrinsics"], volume.voxel * 2)
    import trimesh
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=colors, process=False)
    output.mkdir(parents=True, exist_ok=True)
    mesh.export(output / "mesh.ply")
    report = {"schema": 1, "method": "posed DA3 RGB depth + dense projective TSDF",
              "depth_report": str((Path(depths) / "depths.json").resolve()),
              "source_sha256": dataset["source_sha256"], "resolution": resolution,
              "grid_shape": volume.shape, "voxel_size": volume.voxel, "truncation": volume.truncation,
              "raw_depth_ablation": raw, "min_supporting_other_views": 0 if raw else min_support,
              "surface_band_only_ablation": surface_band_only,
              "relative_depth_tolerance": relative_tolerance, "reprojection_pixel_tolerance": pixel_tolerance,
              "neighbors": neighbors, "min_tsdf_views": min_views, "integrated_views": volume.integrated,
              "topology": topology_metrics(mesh), "colored_vertex_fraction": colored,
              "seconds": time.perf_counter() - started, "coverage": coverage,
              "postprocessing": "none: no smoothing, decimation, component deletion, or hole filling",
              "reference": "derived from splat RGBs; no independent ground-truth geometry"}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "coverage"}, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--depths", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--raw", action="store_true", help="Diagnostic: skip cross-view rejection")
    parser.add_argument("--min-support", type=int, default=1)
    parser.add_argument("--relative-tolerance", type=float, default=0.003)
    parser.add_argument("--pixel-tolerance", type=float, default=1.5)
    parser.add_argument("--neighbors", type=int, default=12)
    parser.add_argument("--min-views", type=int, default=2)
    parser.add_argument("--truncation-voxels", type=float, default=4.0)
    parser.add_argument("--surface-band-only", action="store_true",
                        help="Diagnostic: disable distant free-space carving; may retain doubled surfaces")
    args = parser.parse_args()
    fuse(args.views, args.depths, args.output, args.resolution, args.raw, args.min_support,
         args.relative_tolerance, args.pixel_tolerance, args.neighbors, args.min_views,
         args.truncation_voxels, args.surface_band_only)
