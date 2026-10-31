"""Model-independent calibrated RGB-D helpers; never consume held-out images."""
from __future__ import annotations

import numpy as np
from PIL import Image

from .fusion import project


def training_cameras(dataset):
    """Use only left training cameras, never stereo right or held-out images."""
    cameras = []
    indices = set()
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
        if pair["left"] in {view["rgb"] for view in dataset["evaluation_views"]}:
            raise ValueError("Training/held-out image overlap")
        cameras.append({"index": pair["index"], "rgb": pair["left"], "camera": camera})
    if len(cameras) < 3:
        raise ValueError("Need at least three training cameras")
    return cameras


def nearest_context(cameras, target, count):
    if not 3 <= count <= len(cameras):
        raise ValueError("Context must contain 3..N cameras")
    centers = np.asarray([view["camera"][:3, 3] for view in cameras])
    distances = np.linalg.norm(centers - centers[target], axis=1)
    return [target] + [int(i) for i in np.argsort(distances, kind="stable") if i != target][:count - 1]


def resize_input(rgba, intrinsics, resolution, background=127):
    """Resize RGB, alpha, and K together with integer-pixel-center calibration.

    Inputs are resized here, before DA3, so DA3's resize convention cannot shift
    the known principal point. The model receives already patch-aligned images.
    """
    rgba = np.asarray(rgba)
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError("Expected uint8 RGBA")
    if resolution < 56 or resolution % 14:
        raise ValueError("Processing resolution must be a positive multiple of 14, >=56")
    h, w = rgba.shape[:2]
    if h != w:
        raise ValueError("This prototype requires square, uncropped training renders")
    alpha = rgba[..., 3].astype(np.float32) / 255
    rgb = np.rint(rgba[..., :3] * alpha[..., None] + background * (1 - alpha[..., None])).astype(np.uint8)
    rgb = np.asarray(Image.fromarray(rgb).resize((resolution, resolution), Image.Resampling.LANCZOS))
    alpha = np.asarray(Image.fromarray(alpha).resize((resolution, resolution), Image.Resampling.BOX))
    k = np.asarray(intrinsics, dtype=np.float64).copy()
    sx, sy = resolution / w, resolution / h
    k[0, 0] *= sx
    k[1, 1] *= sy
    k[0, 2] = (k[0, 2] + 0.5) * sx - 0.5
    k[1, 2] = (k[1, 2] + 0.5) * sy - 0.5
    return rgb, alpha, k


def valid_prediction(depth, raw_confidence, alpha, near, far, alpha_threshold=0.2):
    depth, raw_confidence, alpha = [np.asarray(a) for a in (depth, raw_confidence, alpha)]
    if depth.ndim != 2 or raw_confidence.shape != depth.shape or alpha.shape != depth.shape:
        raise ValueError("Expected matching 2D depth, confidence, and alpha")
    if not 0 <= alpha_threshold <= 1 or not 0 < near < far:
        raise ValueError("Invalid depth range or alpha threshold")
    valid = (np.isfinite(depth) & (depth > near) & (depth < far)
             & np.isfinite(raw_confidence) & (raw_confidence > 0)
             & np.isfinite(alpha) & (alpha >= alpha_threshold) & (alpha > 0))
    clean = np.where(valid, depth, 0).astype(np.float32)
    # DA3 confidence is NOT a calibrated probability; map positive scores to
    # bounded soft fusion weights, without deleting a fixed percentage of detail.
    score = np.where(valid, raw_confidence, 0).astype(np.float64)
    weight = (score / (1 + score) * np.where(valid, alpha, 0)).astype(np.float32)
    return clean, weight


def consistent_depth(target, neighbors, min_support=1, relative_tolerance=0.003,
                     absolute_tolerance=0.0, pixel_tolerance=1.5):
    """Reproject through other *predicted* depths, with visibility/depth rejection.

    Support excludes the target itself. All maps carry their own processed K.
    No splat Z, smoothing, depth interpolation, or hole filling is used.
    """
    if (min_support < 1 or relative_tolerance < 0 or absolute_tolerance < 0 or pixel_tolerance <= 0
            or not np.isfinite([relative_tolerance, absolute_tolerance, pixel_tolerance]).all()):
        raise ValueError("Invalid consistency thresholds")
    if min_support > len(neighbors):
        raise ValueError("Not enough neighbors for requested support")
    depth, confidence = target["depth"], target["confidence"]
    yy, xx = np.nonzero((depth > 0) & (confidence > 0))
    k, camera = target["intrinsics"], target["camera"]
    local = np.stack(((xx - k[0, 2]) / k[0, 0], (yy - k[1, 2]) / k[1, 1], np.ones(len(xx))), axis=1)
    points = local * depth[yy, xx, None] @ camera[:3, :3].T + camera[:3, 3]
    support = np.zeros(len(points), dtype=np.uint16)
    for other in neighbors:
        u, v, z, visible = project(points, other["camera"], other["intrinsics"], other["depth"].shape)
        selected = np.flatnonzero(visible)
        d = other["depth"][v[selected], u[selected]]
        weight = other["confidence"][v[selected], u[selected]]
        valid = ((d > 0) & (weight > 0)
                 & (np.abs(d - z[selected]) <= absolute_tolerance + relative_tolerance * z[selected]))
        selected, d = selected[valid], d[valid]
        ok = other["intrinsics"]
        rays = np.stack(((u[selected] - ok[0, 2]) / ok[0, 0],
                         (v[selected] - ok[1, 2]) / ok[1, 1], np.ones(len(selected))), axis=1)
        back_world = (rays * d[:, None]) @ other["camera"][:3, :3].T + other["camera"][:3, 3]
        back = (back_world - camera[:3, 3]) @ camera[:3, :3]
        positive = back[:, 2] > 0
        bx = k[0, 0] * back[:, 0] / np.maximum(back[:, 2], 1e-10) + k[0, 2]
        by = k[1, 1] * back[:, 1] / np.maximum(back[:, 2], 1e-10) + k[1, 2]
        agreed = positive & (np.hypot(bx - xx[selected], by - yy[selected]) <= pixel_tolerance)
        support[selected[agreed]] += 1
    keep = support >= min_support
    result, weights = np.zeros_like(depth), np.zeros_like(confidence)
    result[yy[keep], xx[keep]] = depth[yy[keep], xx[keep]]
    weights[yy[keep], xx[keep]] = confidence[yy[keep], xx[keep]]
    counts = np.zeros(depth.shape, dtype=np.uint16)
    counts[yy, xx] = support
    return result, weights, counts
