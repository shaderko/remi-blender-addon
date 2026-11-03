"""Cylindrical patches and confidence-filtered principal-curvature hints."""

import numpy as np

from .detection import canonical_axis, make_guide, plane_basis


def smooth_patches(face_count, adjacent, smooth):
    neighbors = [[] for _ in range(face_count)]
    for first, second in adjacent[smooth]:
        neighbors[first].append(second)
        neighbors[second].append(first)
    visited = np.zeros(face_count, bool)
    for seed in range(face_count):
        if visited[seed]:
            continue
        patch, stack = [], [seed]
        visited[seed] = True
        while stack:
            current = stack.pop()
            patch.append(current)
            for following in neighbors[current]:
                if not visited[following]:
                    visited[following] = True
                    stack.append(following)
        if len(patch) >= 24:
            yield np.asarray(patch, dtype=np.int32)


def cylinder_guides(vertices, faces, normals, edges, counts, adjacent, sharp,
                    tolerance, target_scale, limit):
    guides, covered = [], set()
    for patch in smooth_patches(len(faces), adjacent, (counts == 2) & ~sharp):
        patch_normals = normals[patch]
        eigenvalues, eigenvectors = np.linalg.eigh(patch_normals.T @ patch_normals / len(patch))
        if eigenvalues[1] < 0.1 or eigenvalues[0] > eigenvalues[1] * 0.025:
            continue
        axis = canonical_axis(eigenvectors[:, 0])
        indices = np.unique(faces[patch])
        points = vertices[indices]
        origin = points.mean(axis=0)
        extent = float(np.linalg.norm(np.ptp(points, axis=0)))
        u, v = plane_basis(axis)
        local = (points - origin) / extent
        xy = np.column_stack((local @ u, local @ v))
        solution, _, rank, _ = np.linalg.lstsq(np.column_stack((2 * xy, np.ones(len(xy)))),
                                             np.sum(xy * xy, axis=1), rcond=None)
        squared = solution[2] + solution[:2] @ solution[:2]
        if rank < 3 or squared <= 0:
            continue
        radius = float(np.sqrt(squared))
        radial = np.linalg.norm(xy - solution[:2], axis=1)
        error = float(np.quantile(np.abs(radial - radius) / radius, 0.95))
        z = local @ axis
        low, high = float(z.min()), float(z.max())
        if error > tolerance or (high - low) < radius * 0.25 or radius * extent < target_scale * 0.6:
            continue
        angles = np.sort(np.arctan2(xy[:, 1] - solution[1], xy[:, 0] - solution[0]))
        if np.diff(np.r_[angles, angles[0] + 2 * np.pi]).max() > np.pi / 3:
            continue
        confidence = float(1 - 0.4 * max(error / tolerance, eigenvalues[0] / (eigenvalues[1] * 0.025)))
        center = origin + extent * (solution[0] * u + solution[1] * v)
        support = vertices[faces[patch]].astype(np.float32)
        radial_normals = support.mean(axis=1) - center
        radial_normals -= (radial_normals @ axis)[:, None] * axis
        normal_sign = 1 if np.sum(radial_normals * patch_normals) >= 0 else -1
        fractions = (0.25, 0.5, 0.75) if (high - low) * extent > target_scale * 6 else (0.5,)
        for fraction in fractions:
            if len(guides) >= limit:
                break
            ring_center = center + axis * extent * (low + fraction * (high - low))
            count = max(32, min(256, int(2 * np.pi * radius * extent / max(target_scale * 0.25, extent / 256))))
            theta = np.arange(count) * (2 * np.pi / count)
            radial = np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v
            ring = ring_center + radius * extent * radial
            guides.append(make_guide(ring, "CYLINDER", "Cylindrical flow ring", confidence=confidence,
                                     normals=radial * normal_sign,
                                     metadata={"center": ring_center, "axis": axis, "radius": radius * extent,
                                               "fit_error": error, "projection_limit": radius * extent * tolerance * 2,
                                               "support_triangles": support}))
        covered.update(int(index) for index in indices)
        if len(guides) >= limit:
            break
    return guides, covered


def curvature_directions(vertices, faces, edges, face_cross):
    """Fit a symmetric shape operator to one-ring normal differences.

    Confidence requires directional anisotropy *and* a consistent local fit.
    Planes have no curvature signal; umbilics (including spheres) have no
    reliable preferred direction. Neither gets arbitrary automatic combs.
    """
    count = len(vertices)
    normals = np.zeros((count, 3), dtype=np.float64)
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_cross)
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-30)
    axes = np.eye(3)[np.argmin(np.abs(normals), axis=1)]
    u = np.cross(normals, axes)
    u /= np.maximum(np.linalg.norm(u, axis=1)[:, None], 1e-30)
    v = np.cross(normals, u)
    first, second = np.concatenate((edges, edges[:, ::-1])).T
    displacement = vertices[second] - vertices[first]
    distance = np.linalg.norm(displacement, axis=1)
    displacement /= np.maximum(distance[:, None], 1e-30)
    dn = (normals[second] - normals[first]) / np.maximum(distance[:, None], 1e-30)
    x = np.einsum("ij,ij->i", displacement, u[first])
    y = np.einsum("ij,ij->i", displacement, v[first])
    nx = np.einsum("ij,ij->i", dn, u[first])
    ny = np.einsum("ij,ij->i", dn, v[first])
    matrix = np.zeros((count, 3, 3))
    rhs = np.zeros((count, 3))
    for row, column, values in ((0, 0, x*x), (0, 1, x*y), (1, 0, x*y),
                                (1, 1, x*x+y*y), (1, 2, x*y), (2, 1, x*y), (2, 2, y*y)):
        np.add.at(matrix[:, row, column], first, values)
    for column, values in ((0, x*nx), (1, y*nx+x*ny), (2, y*ny)):
        np.add.at(rhs[:, column], first, values)
    degree = np.bincount(first, minlength=count)
    matrix += np.eye(3)[None] * (np.trace(matrix, axis1=1, axis2=2) * 1e-7 + 1e-15)[:, None, None]
    coefficients = np.linalg.solve(matrix, rhs[..., None])[..., 0]
    shape = np.empty((count, 2, 2))
    shape[:, 0, 0], shape[:, 0, 1] = coefficients[:, 0], coefficients[:, 1]
    shape[:, 1, 0], shape[:, 1, 1] = coefficients[:, 1], coefficients[:, 2]
    values, vectors = np.linalg.eigh(shape)
    anisotropy = np.abs(values[:, 1] - values[:, 0]) / np.maximum(np.abs(values).sum(axis=1), 1e-30)
    residual = (coefficients[first, 0]*x + coefficients[first, 1]*y - nx)**2
    residual += (coefficients[first, 1]*x + coefficients[first, 2]*y - ny)**2
    fit_error = np.bincount(first, weights=residual, minlength=count)
    signal = np.bincount(first, weights=nx*nx+ny*ny, minlength=count)
    consistency = 1 - np.sqrt(fit_error / np.maximum(signal, 1e-30))
    confidence = np.clip(anisotropy * np.clip(consistency, 0, 1), 0, 1)
    extent = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    confidence[(np.abs(values).max(axis=1) * extent < 0.1) | (degree < 4) | (lengths < 1e-30)] = 0
    dominant = np.argmax(np.abs(values), axis=1)
    principal = vectors[np.arange(count), :, dominant]
    directions = principal[:, :1] * u + principal[:, 1:] * v
    return directions, confidence, np.abs(values).max(axis=1), normals


def curvature_guides(vertices, faces, edges, cross, excluded, target_scale, limit):
    directions, confidence, curvature, normals = curvature_directions(vertices, faces, edges, cross)
    if excluded:
        confidence[list(excluded)] = 0
    edge_length = float(np.median(np.linalg.norm(vertices[edges[:, 1]] - vertices[edges[:, 0]], axis=1)))
    separation = max(target_scale * 2, edge_length * 3)
    accepted, guides = [], []
    for index in np.argsort(-confidence, kind="stable"):
        if confidence[index] < 0.55 or len(guides) >= limit:
            break
        point = vertices[index]
        if accepted and np.linalg.norm(np.asarray(accepted) - point, axis=1).min() < separation:
            continue
        size = min(edge_length * 0.5, 0.15 / max(curvature[index], 1e-30))
        points = np.asarray((point - size * directions[index], point, point + size * directions[index]))
        guides.append(make_guide(points, "CURVATURE", "Curvature flow comb", closed=False, stroke_type=0,
                                 confidence=float(confidence[index]),
                                 normals=np.tile(normals[index], (3, 1)),
                                 metadata={"projection_limit": edge_length * 0.35}))
        accepted.append(point)
    return guides
