"""Surface-guide data and sampling, independent of Blender and the solver."""

from dataclasses import dataclass, field, replace

import numpy as np


@dataclass
class FieldStroke:
    stroke_type: int
    positions: np.ndarray
    normals: np.ndarray
    faces: np.ndarray
    closed: bool = False
    automatic: bool = False
    confidence: float = 1.0
    label: str = "Manual guide"
    kind: str = "MANUAL"
    key: str = ""
    enabled: bool = True
    metadata: dict = field(default_factory=dict)


def resample_path(positions, spacing, *, closed=False, max_points=8192, normals=None):
    """Subdivide segments without shortening a fitted/closed feature path."""
    points = np.asarray(positions, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("Guide positions must be a finite Nx3 array")
    if not np.isfinite(spacing) or spacing <= 0:
        raise ValueError("Guide spacing must be positive")
    if len(points) < 2:
        raise ValueError("A guide requires at least two points")
    values = None if normals is None else np.asarray(normals, dtype=np.float64)
    if values is not None and (values.shape != points.shape or not np.isfinite(values).all()):
        raise ValueError("Guide normals must match the point array")
    epsilon = max(float(np.ptp(points, axis=0).max()) * 1e-8, 1e-15)
    distinct = np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > epsilon]
    points = points[distinct]
    if values is not None:
        values = values[distinct]
    if closed and len(points) > 1 and np.linalg.norm(points[-1] - points[0]) <= epsilon:
        points = points[:-1]
        if values is not None:
            values = values[:-1]
    if len(points) < (3 if closed else 2):
        raise ValueError("The guide has too few distinct points")
    starts = points if closed else points[:-1]
    ends = np.roll(points, -1, axis=0) if closed else points[1:]
    steps = np.maximum(1, np.ceil(np.linalg.norm(ends - starts, axis=1) / spacing).astype(int))
    if steps.sum() + (not closed) > max_points:
        raise ValueError("Guide exceeds the surface-sampling limit")
    chunks = [start + (end - start) * (np.arange(count) / count)[:, None]
              for start, end, count in zip(starts, ends, steps)]
    if not closed:
        chunks.append(points[-1:])
    sampled = np.concatenate(chunks).astype(np.float32)
    if values is None:
        return sampled
    starts = values if closed else values[:-1]
    ends = np.roll(values, -1, axis=0) if closed else values[1:]
    normals = [start + (end - start) * (np.arange(count) / count)[:, None]
               for start, end, count in zip(starts, ends, steps)]
    if not closed:
        normals.append(values[-1:])
    normals = np.concatenate(normals)
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths < 1e-8):
        raise ValueError("Guide crosses incompatible surface normals")
    return sampled, (normals / lengths[:, None]).astype(np.float32)


def reproject_guide(guide, bvh, spacing, max_distance):
    """Recompute hit faces after input subdivision; never reuse old triangle IDs.

    The BVH is injected so the geometry/detection modules need no bpy imports.
    Projection cannot silently bridge a missing part of a detected feature.
    """
    from mathutils import Vector

    has_normals = (guide.normals.shape == guide.positions.shape and
                   np.all(np.linalg.norm(guide.normals, axis=1) > 1e-8))
    sampled = resample_path(guide.positions, spacing, closed=guide.closed,
                            normals=guide.normals if has_normals else None)
    points, expected = sampled if has_normals else (sampled, None)
    positions, normals, faces = [], [], []
    for index, point in enumerate(points):
        hit, normal, face, distance = bvh.find_nearest(Vector(point), max_distance)
        if hit is not None and expected is not None and np.dot(normal, expected[index]) < 0.25:
            compatible = [candidate for candidate in bvh.find_nearest_range(Vector(point), max_distance)
                          if np.dot(candidate[1], expected[index]) >= 0.25]
            if not compatible:
                raise ValueError(f"{guide.label}: projection would jump to an incompatible surface")
            hit, normal, face, distance = min(compatible, key=lambda candidate: candidate[3])
        if hit is None or distance > max_distance:
            raise ValueError(f"{guide.label}: path leaves the source surface")
        positions.append(tuple(hit))
        normals.append(tuple(normal))
        faces.append(face)
    return replace(guide, positions=np.asarray(positions, dtype=np.float32),
                   normals=np.asarray(normals, dtype=np.float32),
                   faces=np.asarray(faces, dtype=np.int32))


def send_guides(session, guides, strength=0.65):
    """One bulk native update; automatic suggestions never become hard locks."""
    guides = [guide for guide in guides if guide.enabled]
    if not guides:
        session.clear_strokes()
        return
    counts = np.asarray([len(guide.positions) for guide in guides], dtype=np.int32)
    session.set_strokes(
        np.asarray([guide.stroke_type for guide in guides], dtype=np.int32),
        np.r_[0, np.cumsum(counts)].astype(np.int32),
        np.concatenate([guide.positions for guide in guides]).astype(np.float32),
        np.concatenate([guide.normals for guide in guides]).astype(np.float32),
        np.concatenate([guide.faces for guide in guides]).astype(np.int32),
        closed=np.asarray([guide.closed for guide in guides], dtype=np.int32),
        weights=np.asarray([min(0.95, strength * guide.confidence) if guide.automatic else 1.0
                            for guide in guides], dtype=np.float32),
        automatic=np.asarray([guide.automatic for guide in guides], dtype=np.int32),
    )


def project_detected_guides(result, bvh, spacing, default_distance):
    projected = []
    support_bvhs = {}
    for guide in result.guides:
        try:
            distance = guide.metadata.get("projection_limit", default_distance)
            support = guide.metadata.get("support_triangles")
            if support is not None:
                # An analytically fitted ring may sit slightly outside its
                # faceted patch. Project to *that* patch first, not a nearby
                # nested/disconnected surface that happens to be closer.
                from mathutils import Vector
                from mathutils.bvhtree import BVHTree

                key = id(support)
                if key not in support_bvhs:
                    points = support.reshape(-1, 3)
                    support_bvhs[key] = BVHTree.FromPolygons(
                        [Vector(p) for p in points], np.arange(len(points)).reshape(-1, 3).tolist(),
                        all_triangles=True,
                    )
                guide = reproject_guide(guide, support_bvhs[key], spacing, distance)
                distance = max(spacing * 0.1, 1e-12)
            projected.append(reproject_guide(guide, bvh, spacing, distance))
        except ValueError as error:
            result.notes.append(str(error))
    return projected
