"""Conservative geometric feature recognition; NumPy only, no scene changes."""

from dataclasses import dataclass, field
import hashlib

import numpy as np

from .guides import FieldStroke


@dataclass
class DetectionResult:
    guides: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def edge_topology(faces):
    """Unique triangle edges, incidence counts and their first two faces."""
    faces = np.asarray(faces, dtype=np.int32)
    halfedges = np.concatenate((faces[:, (0, 1)], faces[:, (1, 2)], faces[:, (2, 0)]))
    halfedges.sort(axis=1)
    edges, inverse, counts = np.unique(halfedges, axis=0, return_inverse=True, return_counts=True)
    order = np.argsort(inverse, kind="stable")
    starts = np.r_[0, np.cumsum(counts[:-1])]
    owners = np.tile(np.arange(len(faces)), 3)[order]
    adjacent = np.full((len(edges), 2), -1, dtype=np.int32)
    adjacent[:, 0] = owners[starts]
    multiple = counts >= 2
    adjacent[multiple, 1] = owners[starts[multiple] + 1]
    return edges, counts, adjacent


def closed_edge_loops(edges):
    """Only unambiguous degree-two components; branches are not guessed through."""
    neighbors = {}
    for first, second in edges:
        neighbors.setdefault(int(first), []).append(int(second))
        neighbors.setdefault(int(second), []).append(int(first))
    visited = set()
    loops = []
    for seed in sorted(neighbors):
        if seed in visited:
            continue
        stack, component = [seed], []
        while stack:
            vertex = stack.pop()
            if vertex in visited:
                continue
            visited.add(vertex)
            component.append(vertex)
            stack.extend(neighbors[vertex])
        if len(component) < 8 or any(len(neighbors[v]) != 2 for v in component):
            continue
        loop, previous, current = [], -1, min(component)
        while not loop or current != loop[0]:
            loop.append(current)
            choices = sorted(neighbors[current])
            following = choices[0] if choices[0] != previous else choices[1]
            previous, current = current, following
        loops.append(np.asarray(loop, dtype=np.int32))
    return loops


def canonical_axis(axis):
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    return axis if axis[np.argmax(np.abs(axis))] >= 0 else -axis


def plane_basis(axis):
    first = np.cross(axis, np.eye(3)[np.argmin(np.abs(axis))])
    first /= np.linalg.norm(first)
    return first, np.cross(axis, first)


def fit_circle(points, tolerance=0.035):
    """Fit an ordered, complete circular feature, not an arbitrary planar arc."""
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 8 or not np.isfinite(points).all():
        return None
    origin = points.mean(axis=0)
    extent = float(np.linalg.norm(np.ptp(points, axis=0)))
    if extent <= 1e-15:
        return None
    local = (points - origin) / extent
    _, singular, basis = np.linalg.svd(local, full_matrices=False)
    if singular[1] < singular[0] * 0.1:
        return None
    axis = canonical_axis(basis[2])
    u, v = plane_basis(axis)
    xy = np.column_stack((local @ u, local @ v))
    solution, _, rank, _ = np.linalg.lstsq(np.column_stack((2 * xy, np.ones(len(xy)))),
                                         np.sum(xy * xy, axis=1), rcond=None)
    radius_squared = solution[2] + solution[:2] @ solution[:2]
    if rank != 3 or radius_squared <= 0:
        return None
    radius = float(np.sqrt(radius_squared))
    centered = xy - solution[:2]
    radial = np.abs(np.linalg.norm(centered, axis=1) - radius) / radius
    planar = np.abs(local @ axis) / radius
    error = max(float(np.quantile(radial, 0.95)), float(np.quantile(planar, 0.95)))
    if error > tolerance or radial.max() > tolerance * 2 or planar.max() > tolerance * 2:
        return None
    angles = np.arctan2(centered[:, 1], centered[:, 0])
    gaps = np.diff(np.r_[np.sort(angles), np.sort(angles)[0] + 2 * np.pi])
    turns = np.angle(np.exp(1j * (np.roll(angles, -1) - angles)))
    if gaps.max() > np.pi / 3 or abs(abs(turns.sum()) - 2 * np.pi) > 0.1:
        return None
    # Reject backtracking/self-crossing point orders even when their fit is good.
    if np.sum(np.abs(turns)) > 2 * np.pi * 1.1:
        return None
    return {"center": origin + extent * (u * solution[0] + v * solution[1]),
            "axis": axis, "radius": radius * extent,
            "confidence": float(1.0 - 0.4 * error / tolerance), "fit_error": error}


def make_guide(points, kind, label, *, closed=True, stroke_type=0, confidence=1.0, metadata=None,
               normals=None):
    points = np.asarray(points, dtype=np.float32)
    # Stable for the same source geometry, independent of solver subdivision.
    metadata = metadata or {}
    payload = (np.r_[metadata["center"], metadata["axis"], metadata["radius"]].astype(np.float64).tobytes()
               if "radius" in metadata else points.tobytes())
    key = kind + ":" + hashlib.sha256(payload).hexdigest()[:16]
    normals = np.zeros_like(points) if normals is None else np.asarray(normals, dtype=np.float32)
    return FieldStroke(stroke_type, points, normals, np.zeros(len(points), np.int32),
                       closed=closed, automatic=True, confidence=confidence, label=label,
                       kind=kind, key=key, metadata=metadata)


def detect_guides(vertices, faces, *, circles=True, cylinders=True, curvature=False,
                  crease_angle=35.0, tolerance=0.035, target_scale=0.0, max_guides=64):
    """Recognize features on the original triangles, before solver subdivision.

    Size/work limits are intentional: dense scans should not freeze a viewport
    while a speculative detector traverses an unbounded graph.
    """
    result = DetectionResult()
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int32)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3
            or not np.isfinite(vertices).all() or not len(faces)
            or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("Feature detection requires finite vertices and valid triangles")
    if not 0 < tolerance <= 0.2 or not 1 <= max_guides <= 256:
        raise ValueError("Invalid feature tolerance or guide limit")
    if len(faces) > 500000 or len(vertices) > 300000:
        result.notes.append("Automatic guides skipped: source exceeds the 500k-triangle/300k-vertex analysis limit")
        return result
    triangle = vertices[faces]
    cross = np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0])
    areas = np.linalg.norm(cross, axis=1)
    extent = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    valid = areas > max(extent * extent * 1e-14, 1e-30)
    if not valid.all():
        result.notes.append("Degenerate source triangles ignored during feature detection")
        faces, cross, areas = faces[valid], cross[valid], areas[valid]
    if not len(faces):
        return result
    normals = cross / areas[:, None]
    edges, counts, adjacent = edge_topology(faces)
    manifold = counts == 2
    sharp = np.zeros(len(edges), bool)
    sharp[manifold] = np.einsum("ij,ij->i", normals[adjacent[manifold, 0]],
                               normals[adjacent[manifold, 1]]) < np.cos(np.radians(crease_angle))
    if np.any(counts > 2):
        result.notes.append("Non-manifold feature junctions are not traversed")
    if circles:
        for kind, mask, label in (("BOUNDARY", counts == 1, "Circular boundary"),
                                  ("CREASE", sharp, "Circular crease")):
            for loop in closed_edge_loops(edges[mask]):
                fitted = fit_circle(vertices[loop], tolerance)
                if fitted is None:
                    continue
                if fitted["radius"] < target_scale * 0.6:
                    result.notes.append("A circular feature is too small for the requested face budget")
                    continue
                if len(result.guides) >= max_guides:
                    break
                result.guides.append(make_guide(vertices[loop], kind, label,
                                                confidence=fitted["confidence"], metadata=fitted))
    covered = set()
    if cylinders and len(result.guides) < max_guides:
        from .rounded import cylinder_guides

        guides, covered = cylinder_guides(vertices, faces, normals, edges, counts, adjacent, sharp,
                                          tolerance, target_scale, max_guides - len(result.guides))
        result.guides.extend(guides)
    if curvature and len(result.guides) < max_guides:
        from .rounded import curvature_guides

        excluded = set(edges[(counts != 2) | sharp].ravel()) | covered
        result.guides.extend(curvature_guides(vertices, faces, edges, cross, excluded,
                                              target_scale, max_guides - len(result.guides)))
    if len(result.guides) >= max_guides:
        result.notes.append(f"Automatic guides limited to {max_guides}; increase the limit to analyze more features")
    if not result.guides:
        result.notes.append("No sufficiently reliable geometric guides found; using the ordinary field solve")
    return result
