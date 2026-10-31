"""Sparse Gaussian-support octree and first-hit implicit-surface ray baking.

The tree is an accelerator, not a voxelized replacement mesh. Gaussians have
finite anisotropic support (3.5 sigma), are owned by exactly one leaf, and each
node bounds the *whole supports*, not just their centers. Field/color/gradient
are evaluated analytically; no reconstructed-mesh normals are sampled.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np

from .projection import C0, _normalize, finish_projection, rasterize_uv


class _SplitBatch(Exception):
    pass


@dataclass
class _Node:
    low: np.ndarray
    high: np.ndarray
    children: tuple
    indices: np.ndarray | None


def ray_box_interval(origins, directions, low, high, lengths):
    """Finite segment/slab intersection, including parallel rays on box faces."""
    parallel = np.abs(directions) < 1e-15
    safe = np.where(parallel, 1.0, directions)
    first, second = (low - origins) / safe, (high - origins) / safe
    near, far = np.minimum(first, second), np.maximum(first, second)
    outside = parallel & ((origins < low) | (origins > high))
    near = np.where(parallel, -np.inf, near)
    far = np.where(parallel, np.inf, far)
    near[outside], far[outside] = np.inf, -np.inf
    enter = np.maximum(np.max(near, axis=1), 0.0)
    leave = np.minimum(np.min(far, axis=1), lengths)
    return enter, leave, enter <= leave


class GaussianOctree:
    def __init__(self, arrays, *, leaf_size=32, max_depth=12, sigma=3.5, cancelled=None):
        self.cancelled = cancelled or (lambda: None)
        self.sigma = float(sigma)
        if not 1 <= leaf_size <= 256 or not 1 <= max_depth <= 20 or not 2 <= sigma <= 6:
            raise ValueError("Invalid octree settings")
        expected = {"positions": 3, "scales": 3, "rotations": 4, "sh_dc": 3, "opacities": 0}
        data = {key: np.asarray(arrays[key], dtype=np.float64) for key in expected}
        count = len(data["positions"])
        if not 0 < count <= 262144:
            raise ValueError("Expected 1–262144 Gaussians")
        for key, width in expected.items():
            if data[key].shape != ((count, width) if width else (count,)) or not np.isfinite(data[key]).all():
                raise ValueError(f"Invalid Gaussian array: {key}")
        if np.any(data["scales"] <= 0) or np.any((data["opacities"] < 0) | (data["opacities"] > 1)):
            raise ValueError("Invalid Gaussian scales or opacity")
        q_length = np.linalg.norm(data["rotations"], axis=1)
        if not np.allclose(q_length, 1, atol=1e-3):
            raise ValueError("Expected unit WXYZ Gaussian rotations")
        keep = data["opacities"] > 0
        if not keep.any():
            raise ValueError("No nontransparent Gaussians")
        self.source_count = count
        self.positions = data["positions"][keep]
        self.opacity = data["opacities"][keep]
        self.colors = np.clip(data["sh_dc"][keep] * C0 + 0.5, 0, 1)
        scales = data["scales"][keep]
        q = data["rotations"][keep] / q_length[keep, None]
        w, x, y, z = q.T
        rotation = np.stack((
            1 - 2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y),
            2*(x*y+w*z), 1 - 2*(x*x+z*z), 2*(y*z-w*x),
            2*(x*z-w*y), 2*(y*z+w*x), 1 - 2*(x*x+y*y),
        ), axis=1).reshape(-1, 3, 3)
        # L maps world displacement into unit Gaussian coordinates; L.T L = Q.
        self.precision_root = rotation.transpose(0, 2, 1) / scales[:, :, None]
        extent = self.sigma * np.sqrt(np.sum((rotation * scales[:, None, :]) ** 2, axis=2))
        lows, highs = self.positions - extent, self.positions + extent
        self.nodes = []
        self.depth = 0

        def build(indices, depth):
            self.cancelled()
            self.depth = max(self.depth, depth)
            node_id = len(self.nodes)
            node = _Node(lows[indices].min(0), highs[indices].max(0), (), indices)
            self.nodes.append(node)
            centers = self.positions[indices]
            center_low, center_high = centers.min(0), centers.max(0)
            if len(indices) <= leaf_size or depth >= max_depth or np.all(center_low == center_high):
                return node_id
            middle = (center_low + center_high) * 0.5
            codes = ((centers >= middle).astype(np.int8) * [1, 2, 4]).sum(1)
            groups = [indices[codes == code] for code in range(8)]
            if max(len(group) for group in groups) == len(indices):
                return node_id
            node.children = tuple(build(group, depth + 1) for group in groups if len(group))
            node.indices = None
            return node_id

        build(np.arange(len(self.positions)), 0)
        self.low, self.high = self.nodes[0].low, self.nodes[0].high
        self.diagonal = float(np.linalg.norm(self.high - self.low))
        if not np.isfinite(self.diagonal) or self.diagonal <= 0:
            raise ValueError("Invalid Gaussian support bounds")

    def _pairs(self, origins, directions, lengths, max_pairs):
        """Return unique ray/Gaussian pairs with exact ellipsoid intervals."""
        stack = [(0, np.arange(len(origins)))]
        parts, total = [], 0
        while stack:
            self.cancelled()
            node_id, rays = stack.pop()
            node = self.nodes[node_id]
            _enter, _leave, intersects = ray_box_interval(
                origins[rays], directions[rays], node.low, node.high, lengths[rays])
            rays = rays[intersects]
            if not len(rays):
                continue
            if node.children:
                stack.extend((child, rays) for child in node.children)
                continue
            # Bound leaf temporaries, even for coincident unsplittable centers.
            for offset in range(0, len(node.indices), 64):
                indices = node.indices[offset:offset + 64]
                rid = np.repeat(rays, len(indices))
                gid = np.tile(indices, len(rays))
                local_o = np.einsum("nij,nj->ni", self.precision_root[gid], origins[rid] - self.positions[gid])
                local_d = np.einsum("nij,nj->ni", self.precision_root[gid], directions[rid])
                a = np.sum(local_d * local_d, axis=1)
                peak = -np.sum(local_o * local_d, axis=1) / a
                at_peak = local_o + peak[:, None] * local_d
                qmin = np.sum(at_peak * at_peak, axis=1)
                half = np.sqrt(np.maximum(self.sigma**2 - qmin, 0) / a)
                enter, leave = np.maximum(peak - half, 0), np.minimum(peak + half, lengths[rid])
                keep = (qmin < self.sigma**2) & (enter < leave)
                if keep.any():
                    part = tuple(value[keep] for value in (rid, gid, a, peak, qmin, enter, leave))
                    total += int(keep.sum())
                    if total > max_pairs:
                        raise _SplitBatch()
                    parts.append(part)
        if not parts:
            return None
        return tuple(np.concatenate([part[i] for part in parts]) for i in range(7))

    def _trace_batch(self, origins, directions, lengths, *, density, max_steps, max_pairs):
        count = len(origins)
        pairs = self._pairs(origins, directions, lengths, max_pairs)
        result = {"hit": np.zeros(count, bool), "colors": np.zeros((count, 3), np.float32),
                  "normals": -directions.astype(np.float32), "distance": np.zeros(count),
                  "normal_agreement": np.zeros(count, np.float32),
                  "step_limit": np.zeros(count, bool), "clipped": np.zeros(count, bool)}
        if pairs is None:
            return result
        rid, gid, a, peak, qmin, enter, leave = pairs
        opacity = self.opacity[gid]
        t = np.full(count, np.inf)
        stop = np.zeros(count)
        np.minimum.at(t, rid, enter)
        np.maximum.at(stop, rid, leave)
        running = np.isfinite(t)
        t[~running] = 0
        previous = t.copy()
        low, high = t.copy(), t.copy()
        epsilon = max(self.diagonal * 1e-10, np.finfo(float).eps)
        spacing = 0.5 / np.sqrt(a)

        def weights_at(distances):
            quad = a * (distances[rid] - peak)**2 + qmin
            return np.where(quad <= self.sigma**2, opacity * np.exp(-0.5 * np.minimum(quad, 1500)), 0)

        for iteration in range(max_steps):
            self.cancelled()
            weights = weights_at(t)
            field = np.bincount(rid, weights=weights, minlength=count)
            if iteration == 0:
                # A cage starting inside the field is not a source surface.
                result["clipped"] = running & (field >= density)
                running[result["clipped"]] = False
            found = running & (field >= density)
            result["hit"] |= found
            low[found], high[found] = previous[found], t[found]
            running[found] = False
            running &= t < stop - epsilon
            if not running.any():
                break
            # Resolved rays must not keep paying for hundreds of steps needed
            # by a few empty/complex rays in the same packet.
            unresolved = running[rid]
            if not unresolved.all():
                rid, gid, a, peak, qmin, enter, leave, opacity, spacing = (
                    value[unresolved] for value in (rid, gid, a, peak, qmin, enter, leave, opacity, spacing))
            active = (enter <= t[rid] + epsilon) & (leave > t[rid] + epsilon)
            step = np.full(count, np.inf)
            # Near-tangent intervals can be much narrower than one ray sigma.
            local_spacing = np.minimum(spacing, (leave - enter) * 0.25)
            np.minimum.at(step, rid[active], local_spacing[active])
            next_event = np.full(count, np.inf)
            future = enter > t[rid] + epsilon
            np.minimum.at(next_event, rid[future], enter[future])
            future_peak = peak > t[rid] + epsilon
            np.minimum.at(next_event, rid[future_peak], peak[future_peak])
            next_t = np.minimum(t + np.maximum(step, epsilon), next_event)
            next_t = np.minimum(next_t, stop)
            previous[running] = t[running]
            t[running] = np.maximum(next_t[running], t[running] + epsilon)
        result["step_limit"] = running & ~result["hit"]
        hit = result["hit"]
        if not hit.any():
            return result
        hit_pairs = hit[pairs[0]]
        rid, gid, a, peak, qmin, enter, leave = (value[hit_pairs] for value in pairs)
        opacity = self.opacity[gid]
        for _ in range(10):
            self.cancelled()
            middle = (low + high) * 0.5
            field = np.bincount(rid, weights=weights_at(middle), minlength=count)
            inside = field >= density
            high[hit & inside], low[hit & ~inside] = middle[hit & inside], middle[hit & ~inside]
        t[hit] = (low[hit] + high[hit]) * 0.5
        weights = weights_at(t) * hit[rid]
        weight_sum = np.bincount(rid, weights=weights, minlength=count)
        hit_points = origins[rid] + t[rid, None] * directions[rid]
        local = np.einsum("nij,nj->ni", self.precision_root[gid], hit_points - self.positions[gid])
        outward = np.einsum("nji,nj->ni", self.precision_root[gid], local)
        gradient = np.zeros((count, 3))
        for channel in range(3):
            result["colors"][:, channel] = np.bincount(
                rid, weights=weights * self.colors[gid, channel], minlength=count) / np.maximum(weight_sum, 1e-20)
            gradient[:, channel] = np.bincount(rid, weights=weights * outward[:, channel], minlength=count)
        magnitude = np.linalg.norm(gradient, axis=1)
        sum_magnitude = np.bincount(rid, weights=weights * np.linalg.norm(outward, axis=1), minlength=count)
        reliable = hit & (magnitude > 1e-10)
        normals = _normalize(gradient)
        flip = np.sum(normals * directions, axis=1) > 0
        normals[flip] *= -1
        result["normals"][reliable] = normals[reliable]
        result["normal_agreement"][reliable] = np.clip(
            magnitude[reliable] / np.maximum(sum_magnitude[reliable], 1e-20), 0, 1)
        result["distance"][hit] = t[hit]
        return result

    def trace(self, origins, directions, lengths, *, density=0.5, max_steps=512,
              chunk=1024, max_pairs=262144, progress=None, backend="NUMPY"):
        origins, directions = np.asarray(origins, float), np.asarray(directions, float)
        if origins.ndim != 2 or origins.shape[1:] != (3,) or directions.shape != origins.shape:
            raise ValueError("Expected matching Nx3 rays")
        lengths = np.broadcast_to(np.asarray(lengths, float), (len(origins),)).copy()
        if (not np.isfinite(origins).all() or not np.isfinite(directions).all()
                or not np.isfinite(lengths).all() or np.any(lengths <= 0)
                or not np.allclose(np.linalg.norm(directions, axis=1), 1, atol=1e-5)):
            raise ValueError("Expected finite unit ray directions and positive lengths")
        if not np.isfinite(density) or density <= 0 or not 8 <= max_steps <= 4096 or chunk < 1 or max_pairs < 1:
            raise ValueError("Invalid ray tracing settings")
        if backend not in {"AUTO", "NUMPY", "NUMBA_CPU"}:
            raise ValueError("Unknown octree tracer backend")
        if not len(origins):
            raise ValueError("No rays to trace")
        self.trace_info = {"tracer_backend": "NUMPY", "tracer_threads": 1}
        # Coherent packets greatly reduce octree traversal. Preserve caller order
        # when returning the maps; this does not change individual ray results.
        middle = origins + directions * lengths[:, None] * 0.5
        cell = np.clip((middle - self.low) / np.maximum(self.high - self.low, 1e-30) * 1023, 0, 1023).astype(np.uint64)
        morton = np.zeros(len(origins), np.uint64)
        for bit in range(10):
            for axis in range(3):
                morton |= ((cell[:, axis] >> bit) & 1) << (3 * bit + axis)
        order = np.argsort(morton, kind="stable")
        origins, directions, lengths = origins[order], directions[order], lengths[order]
        if backend in {"AUTO", "NUMBA_CPU"}:
            try:
                from .octree_native import trace_native
                result = trace_native(self, origins, directions, lengths, density=density,
                                      max_steps=max_steps, progress=progress)
            except (InterruptedError, MemoryError):
                raise
            except Exception as exc:
                if backend != "AUTO":
                    raise
                self.trace_info = {"tracer_backend": "NUMPY", "tracer_threads": 1,
                                   "acceleration_fallback": f"{type(exc).__name__}: {exc}"}
                print(f"Parallel tracer unavailable; using NumPy: {exc}", flush=True)
            else:
                restore = np.argsort(order)
                return {key: value[restore] for key, value in result.items()}
        blocks = []

        def trace_range(start, end):
            try:
                return [self._trace_batch(origins[start:end], directions[start:end], lengths[start:end],
                                          density=density, max_steps=max_steps, max_pairs=max_pairs)]
            except _SplitBatch:
                if end - start == 1:
                    raise ValueError("A ray exceeds the Gaussian candidate memory budget")
                middle = (start + end) // 2
                return trace_range(start, middle) + trace_range(middle, end)

        for start in range(0, len(origins), chunk):
            self.cancelled()
            end = min(start + chunk, len(origins))
            blocks.extend(trace_range(start, end))
            if progress:
                progress(end, len(origins))
        if not blocks:
            raise ValueError("No rays to trace")
        restore = np.argsort(order)
        return {key: np.concatenate([block[key] for block in blocks])[restore] for key in blocks[0]}


def project_octree(mesh, splats, *, texture_size=2048, ray_distance=0.1, density=0.5,
                   max_steps=512, normal_strength=1.0, normal_smoothing=2,
                   fill_holes=True, fill_background=True, margin_px=8,
                   progress=None, cancelled=None, backend="AUTO"):
    """Bake every UV-covered texel through a local, bidirectional projection cage."""
    started = time.perf_counter()
    if not np.isfinite(ray_distance) or not 0 < ray_distance <= 1:
        raise ValueError("Ray distance must be a fraction in (0, 1] of the source bounds diagonal")
    if not np.isfinite(normal_strength) or not 0 <= normal_strength <= 1:
        raise ValueError("Normal strength must be in [0, 1]")
    cancelled = cancelled or (lambda: None)
    if progress:
        progress("Building Gaussian support octree…")
    tree = GaussianOctree(splats, cancelled=cancelled)
    if progress:
        progress("Rasterizing whole-mesh UVs…")
    positions, normals, tangents, uv_sign, covered = rasterize_uv(
        mesh["positions"], mesh["normals"], mesh["uvs"], texture_size,
        corner_tangents=mesh.get("tangents"), tangent_signs=mesh.get("tangent_signs"))
    cancelled()
    if not covered.any():
        raise ValueError("The UV map covers no texels")
    distance = tree.diagonal * ray_distance
    points, reference = positions[covered], normals[covered]
    trace_started = time.perf_counter()

    def trace_progress(done, total):
        elapsed = max(time.perf_counter() - trace_started, 1e-6)
        info = getattr(tree, "trace_info", {})
        if info.get("tracer_backend") == "NUMBA_CPU":
            label = f"CPU ×{info.get('tracer_threads', 1)}"
        else:
            label = "NumPy fallback" if info.get("acceleration_fallback") else "CPU NumPy"
        if done:
            progress(f"Tracing · {done:,}/{total:,} · {done / elapsed:,.0f} rays/s · {label}")
        else:
            progress(f"Preparing parallel tracer · {label} · first run may compile…")

    traced = tree.trace(points + reference * distance, -reference, 2 * distance,
                        density=density, max_steps=max_steps, backend=backend,
                        progress=trace_progress if progress else None)
    cancelled()
    hit = traced["hit"]
    if not hit.any():
        raise ValueError("No Gaussian surface hits. Check source alignment, increase Ray Distance, or adjust Surface Density")
    strength = float(normal_strength)
    sampled = {"colors": traced["colors"],
               "normals": _normalize(reference * (1 - strength) + traced["normals"] * strength),
               "coverage": hit.astype(np.float32), "normal_agreement": traced["normal_agreement"]}
    if progress:
        progress("Assembling ray-baked textures…")
    report = {"method": "OCTREE", "mode": "octree", "gaussians": tree.source_count,
              "normal_source": "GAUSSIAN_SURFACE_GRADIENT", "normal_strength": strength,
              "ray_distance_fraction": ray_distance, "ray_distance_world": distance,
              "surface_density": density, "max_steps": max_steps,
              "rays": len(points), "hits": int(hit.sum()), "misses": int((~hit).sum()),
              "step_limit_rays": int(traced["step_limit"].sum()), "cage_clipped_rays": int(traced["clipped"].sum()),
              "octree_nodes": len(tree.nodes), "octree_depth": tree.depth,
              **tree.trace_info,
              "support_sigma": tree.sigma, "bounds_low": tree.low.tolist(), "bounds_high": tree.high.tolist(),
              "hit_distance_mean": float(np.mean(traced["distance"][hit] - distance)),
              "limitation": "First-hit finite-support Gaussian density isosurface, not ground-truth/PBR or native 3DGS alpha rendering; no mesh detail or silhouette is restored"}
    return finish_projection(mesh, normals, tangents, uv_sign, covered, sampled,
                             texture_size=texture_size, normal_smoothing=normal_smoothing,
                             fill_holes=fill_holes, fill_background=fill_background,
                             margin_px=margin_px, report=report, started=started)
