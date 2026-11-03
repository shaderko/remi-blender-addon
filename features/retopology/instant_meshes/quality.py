"""Topology and guide-following measurements, not just a quad percentage."""

import numpy as np

from .detection import closed_edge_loops, plane_basis


def output_edges(faces):
    faces = np.asarray(faces, dtype=np.int32)
    sizes = np.full(len(faces), faces.shape[1], dtype=np.int32)
    if faces.shape[1] == 4:
        sizes[faces[:, 2] == faces[:, 3]] = 3
    chunks = []
    for corner in range(faces.shape[1]):
        valid = sizes > corner
        chunks.append(np.column_stack((faces[valid, corner], faces[valid, (corner + 1) % sizes[valid]])))
    edges = np.concatenate(chunks)
    edges.sort(axis=1)
    edges, counts = np.unique(edges, axis=0, return_counts=True)
    return edges, counts, sizes


def mesh_quality(vertices, faces):
    vertices, faces = np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int32)
    if not len(faces):
        raise ValueError("Cannot measure an empty mesh")
    edges, counts, sizes = output_edges(faces)
    used = np.unique(faces)
    degree = np.bincount(edges.ravel(), minlength=len(vertices))
    boundary = np.zeros(len(vertices), bool)
    boundary[edges[counts == 1].ravel()] = True
    quads = faces[sizes == 4]
    aspect, angles = np.empty(0), np.empty(0)
    if len(quads):
        points = vertices[quads]
        forward, backward = np.roll(points, -1, axis=1) - points, np.roll(points, 1, axis=1) - points
        lengths = np.linalg.norm(forward, axis=2)
        aspect = lengths.max(axis=1) / np.maximum(lengths.min(axis=1), 1e-30)
        dots = np.sum(forward * backward, axis=2)
        denominator = lengths * np.linalg.norm(backward, axis=2)
        angles = np.abs(np.degrees(np.arccos(np.clip(dots / np.maximum(denominator, 1e-30), -1, 1))) - 90)
    # Parallel-style union/find avoids allocating Python neighbor lists on scans.
    parent = np.arange(len(vertices), dtype=np.int32)
    while True:
        old = parent.copy()
        first, second = parent[edges[:, 0]], parent[edges[:, 1]]
        np.minimum.at(parent, np.maximum(first, second), np.minimum(first, second))
        while not np.array_equal(parent, parent[parent]):
            parent = parent[parent]
        if np.array_equal(old, parent):
            break
    lengths = np.linalg.norm(vertices[edges[:, 0]] - vertices[edges[:, 1]], axis=1)
    expected_degree = np.where(boundary, 3, 4)
    triangle_area = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                                            vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1)
    if faces.shape[1] == 4:
        triangle_area += np.linalg.norm(np.cross(vertices[faces[:, 2]] - vertices[faces[:, 0]],
                                                 vertices[faces[:, 3]] - vertices[faces[:, 0]]), axis=1)
    extent = np.linalg.norm(np.ptp(vertices, axis=0))
    degenerate = triangle_area <= max(extent * extent * 1e-14, 1e-30)
    for corner in range(faces.shape[1]):
        for other in range(corner):
            degenerate |= (sizes > corner) & (faces[:, corner] == faces[:, other])
    return {
        "faces": len(faces), "vertices": len(used), "edges": len(edges),
        "components": len(np.unique(parent[used])), "euler_characteristic": len(used) - len(edges) + len(faces),
        "boundary_edges": int(np.sum(counts == 1)), "nonmanifold_edges": int(np.sum(counts > 2)),
        "degenerate_faces": int(degenerate.sum()), "quad_fraction": float(np.mean(sizes == 4)),
        "irregular_vertices": int(np.sum(degree[used] != expected_degree[used])),
        "interior_poles": int(np.sum((degree[used] != 4) & ~boundary[used])),
        "aspect_p95": float(np.quantile(aspect, 0.95)) if len(aspect) else 0.0,
        "angle_error_p95_deg": float(np.quantile(angles, 0.95)) if len(angles) else 0.0,
        "median_edge_length": float(np.median(lengths)),
    }


def guide_quality(vertices, faces, guides):
    """Measure nearby *tangent-aligned* edges, not merely crossing edges.

    A closed-loop match is deliberately strict: a degree-two cycle in a narrow
    feature tube must wind once around the fitted axis. No guarantee is inferred
    from a smooth-looking orientation field or a high coverage score.
    """
    vertices, faces = np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int32)
    edges, counts, _ = output_edges(faces)
    vectors = vertices[edges[:, 1]] - vertices[edges[:, 0]]
    lengths = np.linalg.norm(vectors, axis=1)
    directions = vectors / np.maximum(lengths[:, None], 1e-30)
    edge_size = max(float(np.median(lengths)), 1e-15)
    tube = edge_size * 0.35
    results = []
    for guide in guides:
        if not guide.enabled:
            continue
        path = np.asarray(guide.positions, dtype=np.float64)
        if len(path) < 3:
            continue
        tangent = np.roll(path, -1, axis=0) - np.roll(path, 1, axis=0)
        if not guide.closed:
            tangent[0], tangent[-1] = path[1] - path[0], path[-1] - path[-2]
        tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-30)
        selected = np.linspace(0, len(path) - 1, min(len(path), 128)).astype(int)
        path, tangent = path[selected], tangent[selected]
        eligible = counts == 1 if guide.kind == "BOUNDARY" else np.ones(len(edges), bool)
        candidate_ids = np.flatnonzero(eligible)
        nearest = np.full(len(path), np.inf)
        nearest_alignment = np.zeros(len(path))
        for begin in range(0, len(candidate_ids), 2048):
            ids = candidate_ids[begin:begin + 2048]
            start = vertices[edges[ids, 0]]
            delta = path[:, None] - start[None]
            t = np.clip(np.sum(delta * vectors[ids][None], axis=2) /
                        np.maximum(lengths[ids]**2, 1e-30)[None], 0, 1)
            distance = np.linalg.norm(delta - t[..., None] * vectors[ids][None], axis=2)
            alignment = np.abs(tangent @ directions[ids].T)
            distance[alignment < np.cos(np.radians(30))] = np.inf
            best = distance.argmin(axis=1)
            values = distance[np.arange(len(path)), best]
            changed = values < nearest
            nearest[changed] = values[changed]
            nearest_alignment[changed] = alignment[np.arange(len(path)), best][changed]
        item = {
            "kind": guide.kind, "label": guide.label, "samples": len(path),
            "aligned_coverage": float(np.mean(nearest <= tube)),
            "path_error_p95_in_edges": float(np.quantile(nearest, 0.95) / edge_size) if np.isfinite(nearest).all() else None,
            "alignment_p95_deg": float(np.quantile(np.degrees(np.arccos(np.clip(nearest_alignment, 0, 1))), 0.95)),
            "closed_loop": None, "circular_deviation_p95_in_radius": None,
        }
        metadata = guide.metadata
        if guide.closed and "radius" in metadata:
            center, axis, radius = np.asarray(metadata["center"]), np.asarray(metadata["axis"]), metadata["radius"]
            midpoint = vertices[edges].mean(axis=1) - center
            axial = midpoint @ axis
            radial = midpoint - axial[:, None] * axis
            tangent = np.cross(axis, radial)
            tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-30)
            mask = (eligible & (np.abs(axial) < tube) &
                    (np.abs(np.linalg.norm(radial, axis=1) - radius) < tube) &
                    (np.abs(np.sum(tangent * directions, axis=1)) > np.cos(np.radians(30))))
            item["closed_loop"] = False
            u, v = plane_basis(axis)
            for loop in closed_edge_loops(edges[mask]):
                local = vertices[loop] - center
                angles = np.arctan2(local @ v, local @ u)
                winding = np.angle(np.exp(1j * (np.roll(angles, -1) - angles))).sum()
                if abs(abs(winding) - 2*np.pi) < 0.1:
                    item["closed_loop"] = True
                    deviation = np.abs(np.linalg.norm(local - (local @ axis)[:, None] * axis, axis=1) - radius) / radius
                    item["circular_deviation_p95_in_radius"] = float(np.quantile(deviation, 0.95))
                    break
        results.append(item)
    return results


def prefer_guided(baseline, guided, baseline_guides, guided_guides):
    """Conservative, explicit preview policy, never a proof of mesh validity."""
    for key in ("degenerate_faces", "nonmanifold_edges", "components"):
        if guided[key] > baseline[key]:
            return False, f"ordinary preview retained: guidance increased {key.replace('_', ' ')}"
    if baseline["boundary_edges"] == 0 and guided["boundary_edges"] > 0:
        return False, "ordinary preview retained: guidance opened the surface"
    if guided["euler_characteristic"] != baseline["euler_characteristic"]:
        return False, "ordinary preview retained: guidance changed topology"
    if guided["aspect_p95"] > baseline["aspect_p95"] * 1.1:
        return False, "ordinary preview retained: guidance increased quad stretching"
    if guided["angle_error_p95_deg"] > baseline["angle_error_p95_deg"] * 1.1 + 1:
        return False, "ordinary preview retained: guidance increased angular distortion"
    baseline_density = baseline["interior_poles"] / baseline["faces"]
    guided_density = guided["interior_poles"] / guided["faces"]
    if guided_density > baseline_density * 1.15 + 2 / baseline["faces"]:
        return False, "ordinary preview retained: guidance added unwanted poles"
    if sum(g["closed_loop"] is True for g in guided_guides) < sum(g["closed_loop"] is True for g in baseline_guides):
        return False, "ordinary preview retained: guidance lost circular edge loops"

    def score(mesh, paths):
        alignment = float(np.mean([g["alignment_p95_deg"] for g in paths])) / 30 if paths else 0
        coverage = 1 - float(np.mean([g["aligned_coverage"] for g in paths])) if paths else 0
        return (mesh["angle_error_p95_deg"] / 30 + max(0, mesh["aspect_p95"] - 1)
                + mesh["interior_poles"] / mesh["faces"] * 10 + alignment + coverage)

    better = score(guided, guided_guides) < score(baseline, baseline_guides) * 0.99
    return better, "guided preview selected" if better else "ordinary preview retained: already as good or better by the measured criteria"
