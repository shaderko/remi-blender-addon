"""Stereo-only metric depth and conservative left/right consistency filtering."""
from __future__ import annotations

import numpy as np


def sample_horizontal(image, coordinates):
    """Linear sampling on each row, with an explicit in-bounds/finite mask."""
    image, coordinates = np.asarray(image), np.asarray(coordinates)
    if image.shape != coordinates.shape or image.ndim != 2:
        raise ValueError("Expected matching HxW arrays")
    height, width = image.shape
    valid = np.isfinite(coordinates) & (coordinates >= 0) & (coordinates <= width - 1)
    safe = np.where(valid, coordinates, 0)
    x0 = np.floor(safe).astype(np.int64)
    x1 = np.minimum(x0 + 1, width - 1)
    fraction = safe - x0
    rows = np.arange(height)[:, None]
    a, b = image[rows, x0], image[rows, x1]
    valid &= np.isfinite(a) & ((fraction == 0) | np.isfinite(b))
    result = np.where(np.isfinite(a), a, 0) * (1 - fraction)
    result += np.where(np.isfinite(b), b, 0) * fraction
    return result, valid


def disparity_to_depth(left, right, alpha_left, alpha_right, *, focal, baseline,
                       near, far, alpha_threshold=0.5, absolute_tolerance=1.0,
                       relative_tolerance=0.01):
    left, right = np.asarray(left, dtype=np.float32), np.asarray(right, dtype=np.float32)
    alpha_left, alpha_right = np.asarray(alpha_left), np.asarray(alpha_right)
    if left.ndim != 2 or any(a.shape != left.shape for a in (right, alpha_left, alpha_right)):
        raise ValueError("Disparities and alpha masks must have the same HxW shape")
    if not all(np.isfinite(v) and v > 0 for v in (focal, baseline, near, far, absolute_tolerance)) or far <= near:
        raise ValueError("Invalid stereo calibration or tolerance")
    if relative_tolerance < 0 or not np.isfinite(relative_tolerance) or not 0 <= alpha_threshold <= 1:
        raise ValueError("Invalid mask/relative tolerance")
    x = np.arange(left.shape[1], dtype=np.float32)[None, :]
    finite_left = np.isfinite(left)
    safe_left = np.where(finite_left, left, 0)
    coordinates = x - safe_left
    sampled, valid = sample_horizontal(right, coordinates)
    right_alpha, alpha_valid = sample_horizontal(alpha_right, coordinates)
    # Both contributing right disparities must be physically valid; do not
    # interpolate a plausible disparity across a zero/invalid prediction.
    support, _ = sample_horizontal((np.isfinite(right) & (right > 0)).astype(float), coordinates)
    valid &= alpha_valid & (support >= 1 - 1e-6) & finite_left & (left > 0)
    valid &= np.isfinite(alpha_left) & (alpha_left >= alpha_threshold) & (right_alpha >= alpha_threshold)
    error = np.abs(safe_left - sampled)
    tolerance = absolute_tolerance + relative_tolerance * np.abs(safe_left)
    valid &= error < tolerance
    depth = np.zeros_like(left)
    np.divide(focal * baseline, left, out=depth, where=valid)
    valid &= np.isfinite(depth) & (depth >= near) & (depth <= far)
    depth[~valid] = 0
    confidence = np.where(valid, np.clip(1 - error / tolerance, 0, 1)
                          * np.minimum(alpha_left, right_alpha), 0).astype(np.float32)
    return depth, confidence
