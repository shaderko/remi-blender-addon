"""Reference-render agreement, not ground-truth geometry accuracy."""
from __future__ import annotations

import numpy as np


def image_metrics(reference, candidate, threshold=0.5):
    reference, candidate = np.asarray(reference), np.asarray(candidate)
    if reference.shape != candidate.shape or reference.ndim != 3 or reference.shape[2] != 4:
        raise ValueError("Expected matching RGBA images")
    masks = [image[..., 3] / 255 >= threshold for image in (reference, candidate)]
    ref, pred = masks
    intersection, union = int((ref & pred).sum()), int((ref | pred).sum())
    if not ref.any():
        raise ValueError("Reference render has no foreground; cannot evaluate")
    common = ref & pred
    mse = float(np.mean((reference[..., :3][common].astype(float)
                         - candidate[..., :3][common].astype(float)) ** 2)) if common.any() else None
    return {"silhouette_iou": intersection / max(union, 1),
            "missing_foreground_fraction": float((ref & ~pred).sum() / ref.sum()),
            "extra_foreground_fraction": float((pred & ~ref).sum() / ref.sum()),
            "common_foreground_psnr_db": float(10 * np.log10(255 ** 2 / max(mse, 1e-10))) if mse is not None else None}


def topology_metrics(mesh):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    faces, vertices = np.asarray(mesh.faces), np.asarray(mesh.vertices)
    edges = np.sort(np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    boundary = unique[counts == 1]
    adjacency = coo_matrix((np.ones(len(unique)), (unique[:, 0], unique[:, 1])),
                           shape=(len(vertices), len(vertices)))
    components, _ = connected_components(adjacency, directed=False)
    return {"vertices": len(vertices), "triangles": len(faces), "components": int(components),
            "boundary_edges": int(len(boundary)),
            "boundary_length": float(np.linalg.norm(vertices[boundary[:, 0]] - vertices[boundary[:, 1]], axis=1).sum()),
            "nonmanifold_edges": int((counts > 2).sum()),
            "note": "Boundaries are diagnostic, not automatically defects: sails/open surfaces may legitimately have them"}
