"""Bounded, confidence-weighted projective TSDF without Open3D native kernels.

Camera/depth units are the archive's original world units. Unknown voxels are
masked during extraction rather than treated as observed empty space. No hole
filling, component deletion, smoothing, or decimation is applied here.
"""
from __future__ import annotations

import numpy as np


def project(points, camera_to_world, intrinsics, shape):
    points = np.asarray(points)
    camera = np.asarray(camera_to_world, dtype=float)
    k = np.asarray(intrinsics, dtype=float)
    local = (points - camera[:3, 3]) @ camera[:3, :3]
    z = local[:, 2]
    denominator = np.maximum(z, 1e-10)
    u = np.floor(k[0, 0] * local[:, 0] / denominator + k[0, 2] + 0.5).astype(np.int64)
    v = np.floor(k[1, 1] * local[:, 1] / denominator + k[1, 2] + 0.5).astype(np.int64)
    valid = (z > 0) & (u >= 0) & (v >= 0) & (u < shape[1]) & (v < shape[0])
    return u, v, z, valid


class TSDF:
    def __init__(self, bounds, resolution=256, truncation_voxels=4.0):
        if not 16 <= resolution <= 512:
            raise ValueError("Grid resolution must be between 16 and 512")
        if not np.isfinite(truncation_voxels) or truncation_voxels < 1:
            raise ValueError("TSDF truncation must be >=1 voxel")
        low, high = np.asarray(bounds["low"], dtype=float), np.asarray(bounds["high"], dtype=float)
        if low.shape != (3,) or high.shape != (3,) or not np.isfinite([low, high]).all() or np.any(high <= low):
            raise ValueError("Invalid fusion bounds")
        # Isotropic voxels: thin axes receive the same physical detail as long axes.
        extent = high - low
        self.voxel = float(extent.max() / (resolution - 1))
        self.truncation = float(truncation_voxels * self.voxel)
        padding = self.truncation * 2
        self.origin = low - padding
        self.shape = tuple((np.ceil((extent + padding * 2) / self.voxel).astype(int) + 1).tolist())
        if np.prod(self.shape) > 150_000_000:
            raise ValueError("Fusion grid exceeds the 150M-voxel research memory limit")
        self.sums = np.zeros(self.shape, dtype=np.float32)
        self.weights = np.zeros(self.shape, dtype=np.float32)
        self.observations = np.zeros(self.shape, dtype=np.uint16)
        self.integrated = 0

    def integrate(self, depth, confidence, camera_to_world, intrinsics, chunk=4, surface_band_only=False):
        depth, confidence = np.asarray(depth), np.asarray(confidence)
        if depth.ndim != 2 or confidence.shape != depth.shape:
            raise ValueError("Expected matching depth/confidence maps")
        if not np.isfinite(depth).all() or not np.isfinite(confidence).all() or np.any(depth < 0):
            raise ValueError("Depth/confidence must be finite and depth nonnegative")
        if np.any((confidence < 0) | (confidence > 1)) or chunk < 1:
            raise ValueError("Invalid confidence or chunk size")
        if not np.any((depth > 0) & (confidence > 0)):
            return
        used = False
        for start in range(0, self.shape[0], chunk):
            stop = min(start + chunk, self.shape[0])
            indices = np.stack(np.meshgrid(np.arange(start, stop), np.arange(self.shape[1]),
                                            np.arange(self.shape[2]), indexing="ij"), axis=-1)
            points = self.origin + indices.reshape(-1, 3) * self.voxel
            u, v, z, valid = project(points, camera_to_world, intrinsics, depth.shape)
            selected = np.flatnonzero(valid)
            d, weight = depth[v[selected], u[selected]], confidence[v[selected], u[selected]]
            sdf = d - z[selected]
            keep = (d > 0) & (weight > 0) & (sdf >= -self.truncation)
            if surface_band_only:
                # Diagnostic only: isolate distant inconsistent depths carving
                # away a surface. This can retain doubled/internal surfaces.
                keep &= sdf <= self.truncation
            selected, sdf, weight = selected[keep], sdf[keep], weight[keep]
            if not len(selected):
                continue
            used = True
            self.sums[start:stop].reshape(-1)[selected] += (
                np.clip(sdf / self.truncation, -1, 1) * weight).astype(np.float32)
            self.weights[start:stop].reshape(-1)[selected] += weight.astype(np.float32)
            observations = self.observations[start:stop].reshape(-1)
            observations[selected] = np.minimum(observations[selected].astype(np.uint32) + 1, 65535)
        self.integrated += int(used)

    def extract(self, min_views=2, min_weight=0.25):
        from skimage.measure import marching_cubes
        if min_views < 1 or not np.isfinite(min_weight) or min_weight <= 0:
            raise ValueError("Minimum observations/weight must be positive")
        observed = (self.observations >= min_views) & (self.weights >= min_weight)
        field = np.ones(self.shape, dtype=np.float32)
        np.divide(self.sums, self.weights, out=field, where=self.weights > 0)
        if not np.any(observed & (field < 0)) or not np.any(observed & (field > 0)):
            raise RuntimeError("No observed surface crossing; add coverage or inspect stereo masks")
        # A crossing must have observed endpoints. Skimage's mask is cell-based,
        # so additionally reject triangles touching a vertex with unknown support.
        vertices, faces, _, _ = marching_cubes(field, level=0, spacing=(self.voxel,) * 3,
                                              mask=observed, allow_degenerate=False,
                                              gradient_direction="descent")
        coordinates = vertices / self.voxel
        lower = np.floor(coordinates + 1e-5).astype(int)
        upper = np.ceil(coordinates - 1e-5).astype(int)
        lower = np.clip(lower, 0, np.array(self.shape) - 1)
        upper = np.clip(upper, 0, np.array(self.shape) - 1)
        supported = np.ones(len(vertices), dtype=bool)
        for x in (lower[:, 0], upper[:, 0]):
            for y in (lower[:, 1], upper[:, 1]):
                for z in (lower[:, 2], upper[:, 2]):
                    supported &= observed[x, y, z]
        faces = faces[supported[faces].all(axis=1)]
        if not len(faces):
            raise RuntimeError("All surface crossings touch unobserved space")
        used, inverse = np.unique(faces, return_inverse=True)
        return (vertices[used] + self.origin).astype(np.float32), inverse.reshape(-1, 3)


def color_vertices(vertices, observations, intrinsics, tolerance):
    """Depth-tested RGB projection; grey marks vertices without a reliable color."""
    colors = np.zeros((len(vertices), 3), dtype=np.float64)
    weights = np.zeros(len(vertices), dtype=np.float64)
    for depth, confidence, rgb, c2w in observations:
        u, v, z, valid = project(vertices, c2w, intrinsics, depth.shape)
        selected = np.flatnonzero(valid)
        d, w = depth[v[selected], u[selected]], confidence[v[selected], u[selected]]
        keep = (d > 0) & (w > 0) & (np.abs(d - z[selected]) <= tolerance)
        selected, w = selected[keep], w[keep]
        colors[selected] += rgb[v[selected], u[selected]] * w[:, None]
        weights[selected] += w
    result = np.full((len(vertices), 3), 127.0)
    np.divide(colors, weights[:, None], out=result, where=weights[:, None] > 0)
    return np.rint(np.clip(result, 0, 255)).astype(np.uint8), float(np.mean(weights > 0))
