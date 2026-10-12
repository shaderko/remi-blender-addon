"""Continuous fitting of rigid UV islands with one shared scale.

Contact-distance gradients move islands apart during projected scale ascent.
Every retained step is checked against triangle geometry, gap and tile bounds.
No island gets an independent scale, shear, or vertex deformation.
"""

import numpy as np

try:
    from ._native import uv_boundaries, boundary_contacts, uv_overlaps
except ImportError:
    uv_boundaries = boundary_contacts = uv_overlaps = None


def fit_islands(uvs, triangles, charts, resolution, gap_px, attempts=18):
    if uv_boundaries is None:
        return np.asarray(uvs, dtype=np.float32)
    original = np.asarray(uvs, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.uint32)
    charts = np.asarray(charts, dtype=np.uint32)
    if not len(charts) or not np.isfinite(original).all():
        return np.asarray(uvs, dtype=np.float32)
    if len(uv_overlaps(original, triangles, 1)["pairs"]):
        return np.asarray(uvs, dtype=np.float32)
    # Compact chart indices; orphan UV loops do not participate in fitting.
    _, charts = np.unique(charts, return_inverse=True)
    charts = charts.astype(np.uint32)
    vertex_charts = np.zeros(len(original), dtype=np.uint32)
    vertex_charts[triangles.reshape(-1)] = np.repeat(charts, 3)
    count = int(charts.max()) + 1
    low = np.full((count, 2), np.inf)
    high = np.full((count, 2), -np.inf)
    np.minimum.at(low, vertex_charts, original)
    np.maximum.at(high, vertex_charts, original)
    centers = (low + high) * 0.5
    offsets = original - centers[vertex_charts]
    half_extents = (high - low) * 0.5
    boundary = uv_boundaries(original, triangles, charts)
    edges = np.asarray(boundary["vertices"])
    edge_charts = np.asarray(boundary["charts"])
    # Float32 output needs a small subpixel cushion; this is not per-side padding.
    gap = (float(gap_px) + 0.002) / max(1, resolution)
    initial_gap = float(boundary_contacts(original[edges], edge_charts, 0.0)["minimum_gap"])
    if initial_gap + 0.001 / resolution < gap_px / resolution:
        return np.asarray(uvs, dtype=np.float32)
    best = original.copy()
    scale = 1.0
    step = 0.008

    for _ in range(attempts):
        proposed_scale = scale * (1.0 + step)
        proposed_centers = centers.copy()
        extent = half_extents * proposed_scale
        if np.any(extent > 0.5):
            step *= 0.5
            if step < 0.00005:
                break
            continue
        accepted = False
        for _projection in range(36):
            proposed_centers = np.clip(proposed_centers, extent, 1.0 - extent)
            current = offsets * proposed_scale + proposed_centers[vertex_charts]
            contacts = np.asarray(boundary_contacts(current[edges], edge_charts, gap)["contacts"])
            if not len(contacts):
                output = current.astype(np.float32)
                final_gap = float(boundary_contacts(output[edges], edge_charts, 0.0)["minimum_gap"])
                if (
                    output.min() >= -1.0e-7 and output.max() <= 1.0000001
                    and final_gap * resolution + 0.001 >= gap_px
                    and not len(uv_overlaps(output, triangles, 1)["pairs"])
                ):
                    best = output.astype(np.float64)
                    centers = proposed_centers
                    scale = proposed_scale
                    accepted = True
                break
            distance = contacts[:, 6]
            # Crossing boundaries require backing off, not an ambiguous force.
            if np.any(distance < 1.0e-12):
                break
            a, b = contacts[:, 0].astype(int), contacts[:, 1].astype(int)
            direction = (contacts[:, 4:6] - contacts[:, 2:4]) / distance[:, None]
            correction = direction * ((gap - distance) * 0.52)[:, None]
            movement = np.zeros((count, 2))
            weight = np.zeros(count)
            np.add.at(movement, a, -correction)
            np.add.at(movement, b, correction)
            np.add.at(weight, a, 1)
            np.add.at(weight, b, 1)
            proposed_centers += movement / np.maximum(1, weight)[:, None]
        step = min(0.008, step * 1.25) if accepted else step * 0.5
        if step < 0.00005:
            break
    return best.astype(np.float32)
