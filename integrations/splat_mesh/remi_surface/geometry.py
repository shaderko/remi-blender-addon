"""Calibrated reconstruction cameras in the selected Gaussian object's local frame."""
from __future__ import annotations

import numpy as np


def look_at(position, target):
    position, target = np.asarray(position, dtype=float), np.asarray(target, dtype=float)
    forward = target - position
    length = np.linalg.norm(forward)
    if not np.isfinite(length) or length < 1e-10:
        raise ValueError("Camera position must differ from its target")
    forward /= length
    up = np.array([0.0, 0.0, 1.0])
    if abs(forward @ up) > 0.99:
        up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    matrix = np.eye(4)
    matrix[:3, :3] = np.stack((right, down, forward), axis=1)
    matrix[:3, 3] = position
    return matrix


def stereo_cameras(position, target, baseline):
    if not np.isfinite(baseline) or baseline <= 0:
        raise ValueError("Stereo baseline must be positive")
    center = look_at(position, target)
    left, right = center.copy(), center.copy()
    offset = center[:3, 0] * baseline * 0.5
    left[:3, 3] -= offset
    right[:3, 3] += offset
    return left, right


def make_rig(bounds, views=48, evaluation_views=12, resolution=1024,
             distance=3.4, baseline_fraction=0.07, lens=50.0, sensor_width=36.0):
    if views < 2 or evaluation_views < 1 or resolution < 64:
        raise ValueError("Need >=2 stereo views, >=1 held-out view, and resolution >=64")
    radius = float(bounds["radius"])
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("Bounds radius must be positive")
    if not all(np.isfinite(v) and v > 0 for v in (distance, baseline_fraction, lens, sensor_width)):
        raise ValueError("Invalid camera parameters")
    center = np.asarray(bounds["center"], dtype=float)
    if center.shape != (3,) or not np.isfinite(center).all():
        raise ValueError("Invalid bounds center")
    baseline = 2 * radius * baseline_fraction
    # The object fits inside this sphere, including the shifted stereo cameras.
    half_fov = np.arctan(sensor_width / (2 * lens))
    if distance <= 1 / np.sin(half_fov) + baseline_fraction:
        raise ValueError("Camera distance is too small to frame the bounds sphere")
    focal = resolution * lens / sensor_width
    principal = (resolution - 1) / 2  # integer indices denote pixel centers
    intrinsics = [[focal, 0.0, principal], [0.0, focal, principal], [0.0, 0.0, 1.0]]
    total = views + evaluation_views
    # Interleave held-out cameras across the sphere, not in a single polar cap.
    held_out = set(np.floor((np.arange(evaluation_views) + 0.5) * total / evaluation_views).astype(int))
    pairs, evaluations = [], []
    for index in range(total):
        z = 1 - 2 * (index + 0.5) / total
        theta = np.pi * (3 - np.sqrt(5)) * index
        direction = np.array([np.sqrt(1 - z * z) * np.cos(theta),
                              np.sqrt(1 - z * z) * np.sin(theta), z])
        position = center + radius * distance * direction
        if index in held_out:
            camera = look_at(position, center)
            evaluations.append({"index": len(evaluations),
                                "rgb": f"evaluation/view_{len(evaluations):04d}.png",
                                "camera_to_world_opencv": camera.tolist()})
        else:
            left, right = stereo_cameras(position, center, baseline)
            pair_index = len(pairs)
            pairs.append({"index": pair_index, "baseline": baseline,
                          "left": f"pairs/pair_{pair_index:04d}/left.png",
                          "right": f"pairs/pair_{pair_index:04d}/right.png",
                          "left_camera_to_world_opencv": left.tolist(),
                          "right_camera_to_world_opencv": right.tolist()})
    return {"schema": 1, "resolution": [resolution, resolution],
            "intrinsics": intrinsics, "bounds": bounds, "distance": distance,
            "near": radius * 0.01, "far": radius * (distance + 2),
            "lens_mm": lens, "sensor_width_mm": sensor_width,
            "pairs": pairs, "evaluation_views": evaluations,
            "convention": "OpenCV c2w; +Z forward, +Y down; integer pixel centers; positive disparity = x_left - x_right"}


def archive_bounds(arrays):
    """Full nontransparent center bounds, not quantiles that discard thin parts."""
    keep = arrays["opacities"] >= 0.02
    if not np.any(keep):
        raise ValueError("No Gaussians with opacity >=0.02")
    points = arrays["positions"][keep].astype(float)
    pad = float(np.median(arrays["scales"][keep])) * 3
    low, high = points.min(0) - pad, points.max(0) + pad
    center = (low + high) / 2
    radius = max(float(np.linalg.norm(high - low) / 2), 1e-4)
    return {"low": low.tolist(), "high": high.tolist(), "center": center.tolist(),
            "radius": radius, "policy": "full centers with opacity >=0.02, padded by 3 median scales"}


def training_cameras(dataset):
    cameras, indices = [], set()
    held_out = {view["rgb"] for view in dataset.get("evaluation_views", [])}
    for pair in dataset["pairs"]:
        if pair["index"] in indices:
            raise ValueError("Duplicate training camera index")
        indices.add(pair["index"])
        camera = np.asarray(pair["left_camera_to_world_opencv"], dtype=np.float64)
        if (camera.shape != (4, 4) or not np.isfinite(camera).all()
                or not np.allclose(camera[3], [0, 0, 0, 1])
                or not np.allclose(camera[:3, :3].T @ camera[:3, :3], np.eye(3), atol=1e-5)
                or not np.isclose(np.linalg.det(camera[:3, :3]), 1)):
            raise ValueError("Expected rigid OpenCV camera-to-world matrices")
        if pair["left"] in held_out:
            raise ValueError("Training/held-out image overlap")
        cameras.append({"index": pair["index"], "rgb": pair["left"], "camera": camera})
    if len(cameras) < 3:
        raise ValueError("Need at least three training cameras")
    return cameras
