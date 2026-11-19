"""Reconstruct render seams in an analysis copy, never weld an asset in place.

Only uniquely paired, oppositely directed boundary edges at EXACTLY identical
positions are candidates. Existing interior edges, ambiguous geometric edges,
duplicate coincident faces and distinct objects are not indiscriminately welded.
No tolerance-based merging, coordinate movement, face deletion or attribute edits.
"""

import numpy as np


def consistent_winding(faces):
    """Check oriented shared edges on homogeneous triangle or quad arrays."""
    faces = np.asarray(faces)
    half = np.stack((faces, np.roll(faces, -1, axis=1)), axis=-1).reshape(-1, 2)
    _edges, inverse, counts = np.unique(np.sort(half, axis=1), axis=0,
                                        return_inverse=True, return_counts=True)
    winding = np.bincount(inverse, weights=np.where(half[:, 0] < half[:, 1], 1, -1))
    return bool(not np.any(counts > 2) and np.all(winding[counts == 2] == 0))


def stitch_render_seams(vertices, faces, tick=lambda: None):
    """Cooperative generator returning (analysis vertices, triangles, report)."""
    vertices, faces = np.asarray(vertices), np.asarray(faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all() or
            faces.ndim != 2 or faces.shape[1] != 3 or not len(faces) or
            faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("Render seam analysis requires finite source triangles")
    report = {"enabled": True, "source_vertices": len(vertices), "analysis_vertices": len(vertices),
              "stitched_vertices": 0, "seam_edge_pairs": 0,
              "ambiguous_geometric_edges": 0, "ambiguous_render_edges": 0,
              "duplicate_geometric_faces": 0}
    half = faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2)
    _edges, topology_inverse, topology_counts = np.unique(
        np.sort(half, axis=1), axis=0, return_inverse=True, return_counts=True)
    boundary = topology_counts[topology_inverse] == 1
    tick()
    yield
    if not boundary.any():
        return vertices, faces, report
    _positions, position_ids = np.unique(vertices, axis=0, return_inverse=True)
    if len(_positions) == len(vertices):
        return vertices, faces, report
    geometric_half = position_ids[half]
    _geometric_edges, geometric_inverse, geometric_counts = np.unique(
        np.sort(geometric_half, axis=1), axis=0, return_inverse=True, return_counts=True)
    report["ambiguous_geometric_edges"] = int(np.count_nonzero(geometric_counts > 2))
    geometric_faces = np.sort(position_ids[faces], axis=1)
    _face_keys, face_inverse, face_counts = np.unique(geometric_faces, axis=0, return_inverse=True, return_counts=True)
    duplicated = face_counts[face_inverse] > 1
    report["duplicate_geometric_faces"] = int(duplicated.sum())
    ambiguous = boundary & (geometric_counts[geometric_inverse] > 2) & ~np.repeat(duplicated, 3)
    report["ambiguous_render_edges"] = int(len(np.unique(geometric_inverse[ambiguous])))
    order = np.argsort(geometric_inverse, kind="stable")
    starts = np.r_[0, np.cumsum(geometric_counts[:-1])][geometric_counts == 2]
    first, second = order[starts], order[starts+1]
    eligible = (boundary[first] & boundary[second] &
                np.all(geometric_half[first] == geometric_half[second, ::-1], axis=1) &
                ~duplicated[first//3] & ~duplicated[second//3])
    first, second = first[eligible], second[eligible]
    report["seam_edge_pairs"] = len(first)
    tick()
    yield
    if not len(first):
        return vertices, faces, report
    left = np.r_[half[first, 0], half[first, 1]]
    right = np.r_[half[second, 1], half[second, 0]]
    parent = np.arange(len(vertices), dtype=np.int32)
    while True:
        old = parent.copy()
        a, b = parent[left], parent[right]
        np.minimum.at(parent, np.maximum(a, b), np.minimum(a, b))
        while not np.array_equal(parent, parent[parent]):
            parent = parent[parent]
        tick()
        yield
        if np.array_equal(old, parent):
            break
    remapped = parent[faces]
    used, inverse = np.unique(remapped, return_inverse=True)
    prepared_vertices = np.array(vertices[used], copy=True)
    prepared_faces = inverse.reshape(-1, 3).astype(np.int32)
    report["analysis_vertices"] = len(prepared_vertices)
    report["stitched_vertices"] = int(len(np.unique(faces))-len(prepared_vertices))
    return prepared_vertices, prepared_faces, report
