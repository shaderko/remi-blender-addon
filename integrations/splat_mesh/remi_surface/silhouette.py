"""Reject mesh geometry contradicted by calibrated training foreground masks.

This is an explicit visual-hull consistency gate, not hole filling, component
deletion by size, or a guarantee of correct concave geometry. Never use holdouts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageFilter

from .common import sha256
from .geometry import training_cameras
from .metrics import topology_metrics


def silhouette_support(points, cameras, masks, intrinsics, near, max_background=3,
                       min_foreground=3, device="cuda", chunk=262144, progress=None, cancelled=None):
    import torch
    if (not cameras or len(cameras) != len(masks) or max_background < 0
            or not 1 <= min_foreground <= len(cameras) or chunk < 1):
        raise ValueError("Invalid silhouette support configuration")
    k = np.asarray(intrinsics)
    if k.shape != (3, 3) or not np.isfinite(k).all() or not np.isfinite(near) or near <= 0:
        raise ValueError("Invalid silhouette calibration")
    points = np.asarray(points)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("Expected finite Nx3 world points")
    shapes = {np.asarray(mask).shape for mask in masks}
    if len(shapes) != 1 or len(next(iter(shapes))) != 2:
        raise ValueError("Expected matching 2D masks")
    h, w = next(iter(shapes))
    prepared = [(torch.as_tensor(c[:3, 3], dtype=torch.float32, device=device),
                 torch.as_tensor(c[:3, :3], dtype=torch.float32, device=device),
                 torch.as_tensor(mask, dtype=torch.bool, device=device)) for c, mask in zip(cameras, masks)]
    accepted = np.zeros(len(points), dtype=bool)
    with torch.inference_mode():
        for start in range(0, len(points), chunk):
            if cancelled is not None:
                cancelled()
            # Trimesh may expose read-only cached arrays. Copy this bounded
            # chunk explicitly rather than create a tensor aliasing them.
            p = torch.as_tensor(np.array(points[start:start + chunk], dtype=np.float32, copy=True), device=device)
            foreground = torch.zeros(len(p), dtype=torch.int32, device=device)
            background = torch.zeros_like(foreground)
            for center, rotation, mask in prepared:
                local = (p - center) @ rotation
                z = local[:, 2]
                u = torch.floor(k[0, 0] * local[:, 0] / z.clamp_min(1e-10) + k[0, 2] + 0.5).long()
                v = torch.floor(k[1, 1] * local[:, 1] / z.clamp_min(1e-10) + k[1, 2] + 0.5).long()
                in_frame = (z > near) & (u >= 0) & (u < w) & (v >= 0) & (v < h)
                hit = mask[v.clamp(0, h - 1), u.clamp(0, w - 1)]
                foreground += (in_frame & hit).to(torch.int32)
                background += (in_frame & ~hit).to(torch.int32)
            accepted[start:start + len(p)] = ((foreground >= min_foreground) & (background <= max_background)).cpu().numpy()
            if progress is not None:
                progress(min(start + len(p), len(points)), len(points))
    return accepted


def constrain(views, source_mesh, output, alpha_threshold=0.02, padding=2,
              max_background=3, min_foreground=3, device="cuda", progress=None, cancelled=None):
    views, source_mesh, output = [Path(p).resolve() for p in (views, source_mesh, output)]
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    if not 0 < alpha_threshold <= 1 or not 0 <= padding <= 20:
        raise ValueError("Invalid foreground threshold or pixel padding")
    dataset = json.loads((views / "dataset.json").read_text())
    if sha256(dataset["source_archive"]) != dataset["source_sha256"]:
        raise ValueError("Source archive changed since rendering")
    if not (views / "render_complete.json").is_file():
        raise ValueError("Incomplete renders")
    views_list = training_cameras(dataset)
    masks = []
    for view in views_list:
        if cancelled is not None:
            cancelled()
        with Image.open(views / view["rgb"]) as image:
            if image.size != tuple(dataset["resolution"]):
                raise ValueError("Mask/calibration size mismatch")
            mask = Image.fromarray((np.asarray(image.convert("RGBA"))[..., 3] / 255 >= alpha_threshold).astype(np.uint8) * 255)
            if padding:
                mask = mask.filter(ImageFilter.MaxFilter(2 * padding + 1))
            masks.append(np.asarray(mask) > 0)
    import trimesh
    mesh = trimesh.load(source_mesh, process=False, force="mesh")
    before = topology_metrics(mesh)
    started = time.perf_counter()
    cameras = [view["camera"] for view in views_list]
    kwargs = {"max_background": max_background, "min_foreground": min_foreground,
              "device": device, "cancelled": cancelled}
    if progress:
        progress("Checking silhouette vertices…")
    supported_vertices = silhouette_support(mesh.vertices, cameras, masks, dataset["intrinsics"], dataset["near"], **kwargs)
    if progress:
        progress("Checking silhouette faces…")
    # Also test face interiors: long triangles can bridge empty space even when
    # their endpoints happen to lie inside the visual hull.
    supported_centers = silhouette_support(mesh.triangles_center, cameras, masks, dataset["intrinsics"], dataset["near"], **kwargs)
    keep = supported_vertices[mesh.faces].all(axis=1) & supported_centers
    if not keep.any():
        raise RuntimeError("Silhouette constraints reject all faces; inspect calibration/masks")
    rejected_faces = int((~keep).sum())
    mesh.update_faces(keep)
    mesh.remove_unreferenced_vertices()
    output.mkdir(parents=True, exist_ok=True)
    mesh.export(output / "mesh.ply")
    report = {"schema": 1, "method": "explicit training-silhouette consistency gate",
              "input_mesh": str(source_mesh), "input_mesh_sha256": sha256(source_mesh),
              "source_sha256": dataset["source_sha256"], "dataset_sha256": sha256(views / "dataset.json"),
              "alpha_threshold": alpha_threshold, "foreground_padding_pixels": padding,
              "max_background_votes": max_background, "min_foreground_votes": min_foreground,
              "training_views": len(cameras), "held_out_views_used": 0,
              "before": before, "after": topology_metrics(mesh), "rejected_faces": rejected_faces,
              "seconds": time.perf_counter() - started,
              "unknown_space": "offscreen/behind-camera samples do not vote as foreground or background",
              "limitations": "sampled vertices/centroids only; visual hull cannot establish concave geometry",
              "postprocessing": "no smoothing, decimation, hole filling, or component deletion by size"}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alpha-threshold", type=float, default=0.02)
    parser.add_argument("--padding", type=int, default=2)
    parser.add_argument("--max-background", type=int, default=3)
    parser.add_argument("--min-foreground", type=int, default=3)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    constrain(args.views, args.mesh, args.output, args.alpha_threshold, args.padding,
              args.max_background, args.min_foreground, args.device)
