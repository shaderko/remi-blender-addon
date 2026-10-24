"""Project Gaussian-splat appearance onto a UV-mapped mesh.

The mesh is the geometric target. Each texel is rasterized to a world-space
surface point and normal, then the Gaussian field is evaluated at that point.
This is a bake, not a channel copy: standard 3DGS stores view-dependent SH
radiance, opacity, and covariance. The projected base colour is therefore an
appearance colour (with baked lighting), and the normal is a confidence-bearing
surface hypothesis.

The implementation is NumPy/SciPy only so it can run in the existing external
Python worker without a CUDA rasterizer.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import time

import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter, map_coordinates
from scipy.spatial import cKDTree

C0 = 0.28209479177387814
NORMAL_SOURCES = ("AUTO", "GRADIENT", "COVARIANCE", "MESH")


@dataclass(frozen=True)
class ProjectionResult:
    albedo: np.ndarray
    normal: np.ndarray
    confidence: np.ndarray
    coverage: np.ndarray
    normal_agreement: np.ndarray
    covered: np.ndarray
    report: dict


@dataclass(frozen=True)
class ViewProjectionResult:
    layer: np.ndarray
    report: dict


def _normalize(values, axis=-1):
    length = np.linalg.norm(values, axis=axis, keepdims=True)
    return values / np.maximum(length, 1e-8)


def _inverse_covariance(scales, rotations):
    """Return per-splat Sigma^-1 from linear scales and WXYZ quaternions."""
    q = rotations / np.maximum(np.linalg.norm(rotations, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q.T
    r = np.stack(
        (
            1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
            2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
            2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
        ),
        axis=1,
    ).reshape(-1, 3, 3)
    inv_s2 = 1.0 / np.maximum(scales, 1e-8) ** 2
    return np.einsum("nij,nj,nkj->nik", r, inv_s2, r)


def _covariance(scales, rotations):
    q = rotations / np.maximum(np.linalg.norm(rotations, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q.T
    r = np.stack(
        (
            1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
            2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
            2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
        ),
        axis=1,
    ).reshape(-1, 3, 3)
    s2 = np.maximum(scales, 1e-8) ** 2
    return np.einsum("nij,nj,nkj->nik", r, s2, r)


def prepare_splats(arrays):
    """Precompute splat fields used by the projector."""
    positions = np.asarray(arrays["positions"], dtype=np.float32)
    scales = np.asarray(arrays["scales"], dtype=np.float32)
    rotations = np.asarray(arrays["rotations"], dtype=np.float32)
    opacities = np.asarray(arrays["opacities"], dtype=np.float32).reshape(-1)
    sh_dc = np.asarray(arrays["sh_dc"], dtype=np.float32)
    if not (len(positions) == len(scales) == len(rotations) == len(opacities) == len(sh_dc)):
        raise ValueError("Gaussian arrays have inconsistent lengths")
    if len(positions) == 0:
        raise ValueError("Gaussian archive is empty")

    cov = _covariance(scales, rotations)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    order = np.argsort(eigenvalues, axis=1)
    eigenvalues = np.take_along_axis(eigenvalues, order, axis=1)
    # eigh returns ascending eigenvalues already; this keeps the contract explicit.
    normals = eigenvectors[:, :, 0].astype(np.float32)
    flatness = 1.0 - np.sqrt(
        np.clip(eigenvalues[:, 0] / np.maximum(eigenvalues[:, 1], 1e-12), 0.0, 1.0)
    )

    return {
        "positions": positions,
        "opacities": opacities,
        "inv_cov": _inverse_covariance(scales, rotations).astype(np.float32),
        "cov_normals": normals,
        "flatness": flatness.astype(np.float32),
        "colors": np.clip(sh_dc * C0 + 0.5, 0.0, 1.0).astype(np.float32),
        "tree": cKDTree(positions),
    }


def rasterize_uv(positions, normals, uvs, size):
    """Rasterize per-triangle positions/normals/UVs into texture-space fields.

    Inputs are per-corner triangle arrays:
      positions (T, 3, 3), normals (T, 3, 3), uvs (T, 3, 2).

    Returns world position, world normal, tangent, UV handedness, and a covered
    mask at the requested square texture size.
    """
    positions = np.asarray(positions, dtype=np.float32)
    normals = np.asarray(normals, dtype=np.float32)
    uvs = np.asarray(uvs, dtype=np.float32)
    if positions.ndim != 3 or positions.shape[1:] != (3, 3):
        raise ValueError("Mesh positions must be a (triangles, 3, 3) array")
    if normals.shape != positions.shape:
        raise ValueError("Mesh normals must match positions")
    if uvs.shape != (len(positions), 3, 2):
        raise ValueError("Mesh UVs must be a (triangles, 3, 2) array")
    size = int(size)
    if not 64 <= size <= 8192:
        raise ValueError("Texture size must be between 64 and 8192")

    pos_img = np.zeros((size, size, 3), dtype=np.float32)
    nrm_img = np.zeros((size, size, 3), dtype=np.float32)
    tan_img = np.zeros((size, size, 3), dtype=np.float32)
    sign_img = np.ones((size, size), dtype=np.int8)
    covered = np.zeros((size, size), dtype=bool)

    eps = 1e-6
    for index in range(len(positions)):
        tri = positions[index]
        tri_n = normals[index]
        tri_uv = uvs[index]
        x = tri_uv[:, 0] * size
        y = (1.0 - tri_uv[:, 1]) * size
        xmin = max(0, int(math.floor(float(x.min()))) - 1)
        xmax = min(size - 1, int(math.ceil(float(x.max()))) + 1)
        ymin = max(0, int(math.floor(float(y.min()))) - 1)
        ymax = min(size - 1, int(math.ceil(float(y.max()))) + 1)
        if xmin > xmax or ymin > ymax:
            continue

        xs = np.arange(xmin, xmax + 1, dtype=np.float32) + 0.5

        x0, y0 = float(x[0]), float(y[0])
        x1, y1 = float(x[1]), float(y[1])
        x2, y2 = float(x[2]), float(y[2])
        denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denominator) < 1e-12:
            continue

        duv1 = tri_uv[1] - tri_uv[0]
        duv2 = tri_uv[2] - tri_uv[0]
        dp1 = tri[1] - tri[0]
        dp2 = tri[2] - tri[0]
        determinant = duv1[0] * duv2[1] - duv2[0] * duv1[1]
        if abs(determinant) > 1e-12:
            tangent_triangle = (dp1 * duv2[1] - dp2 * duv1[1]) / determinant
            uv_sign = 1 if determinant > 0.0 else -1
        else:
            tangent_triangle = np.array((1.0, 0.0, 0.0), dtype=np.float32)
            uv_sign = 1

        # A single quad can cover the whole texture. Rasterize in row bands so
        # its intermediate arrays stay bounded at 4K instead of allocating the
        # full UV bounding box at once.
        for band_start in range(ymin, ymax + 1, 256):
            band_end = min(ymax, band_start + 255)
            ys = np.arange(band_start, band_end + 1, dtype=np.float32) + 0.5
            grid_x, grid_y = np.meshgrid(xs, ys)

            w0 = ((y1 - y2) * (grid_x - x2) + (x2 - x1) * (grid_y - y2)) / denominator
            w1 = ((y2 - y0) * (grid_x - x2) + (x0 - x2) * (grid_y - y2)) / denominator
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -eps) & (w1 >= -eps) & (w2 >= -eps)
            if not inside.any():
                continue

            bary = np.stack((w0, w1, w2), axis=-1)
            point = np.einsum("...k,kj->...j", bary, tri)
            normal = np.einsum("...k,kj->...j", bary, tri_n)
            normal = _normalize(normal)

            tangent = tangent_triangle - normal * np.sum(
                tangent_triangle * normal, axis=-1, keepdims=True
            )
            tangent = _normalize(tangent)

            yy, xx = np.nonzero(inside)
            gy = yy + band_start
            gx = xx + xmin
            pos_img[gy, gx] = point[inside]
            nrm_img[gy, gx] = normal[inside]
            tan_img[gy, gx] = tangent[inside]
            sign_img[gy, gx] = uv_sign
            covered[gy, gx] = True

    return pos_img, nrm_img, tan_img, sign_img, covered


def sample_surface(
    points,
    mesh_normals,
    tangents,
    prepared,
    *,
    samples=16,
    normal_source="AUTO",
    normal_strength=1.0,
    chunk=131072,
):
    """Evaluate the Gaussian field at world-space texel samples."""
    points = np.asarray(points, dtype=np.float32)
    mesh_normals = _normalize(np.asarray(mesh_normals, dtype=np.float32))
    tangents = _normalize(np.asarray(tangents, dtype=np.float32))
    count = len(points)
    if count == 0:
        raise ValueError("The UV map covers no texels")

    k = max(1, min(int(samples), len(prepared["positions"])))
    colors = np.zeros((count, 3), dtype=np.float32)
    normal_field = np.zeros((count, 3), dtype=np.float32)
    coverage = np.zeros(count, dtype=np.float32)
    normal_agreement = np.zeros(count, dtype=np.float32)
    flatness = np.zeros(count, dtype=np.float32)

    centers = prepared["positions"]
    inv_cov = prepared["inv_cov"]
    opacity = prepared["opacities"]
    splat_colors = prepared["colors"]
    cov_normals = prepared["cov_normals"]
    splat_flatness = prepared["flatness"]
    tree = prepared["tree"]

    for start in range(0, count, chunk):
        end = min(start + chunk, count)
        block = points[start:end]
        block_n = mesh_normals[start:end]
        _distances, indices = tree.query(block, k=k, workers=-1)
        if k == 1:
            indices = indices[:, None]
        delta = block[:, None, :] - centers[indices]
        inv_delta = np.einsum("bkij,bkj->bki", inv_cov[indices], delta)
        quad = np.sum(delta * inv_delta, axis=-1)
        weights = opacity[indices] * np.exp(-0.5 * np.clip(quad, 0.0, 60.0))
        weights[quad > 9.0] = 0.0
        weight_sum = np.sum(weights, axis=1)
        safe_sum = np.maximum(weight_sum, 1e-8)

        colors[start:end] = (
            np.sum(weights[..., None] * splat_colors[indices], axis=1)
            / safe_sum[:, None]
        )
        coverage[start:end] = 1.0 - np.exp(-weight_sum)

        # Covariance normal: per-splat shortest axis, oriented to the mesh
        # normal before aggregation. Agreement measures how consistently the
        # neighbourhood votes for one normal.
        candidates = cov_normals[indices]
        signs = np.where(
            np.sum(candidates * block_n[:, None, :], axis=-1) < 0.0, -1.0, 1.0
        )
        candidates = candidates * signs[..., None]
        summed = np.sum(weights[..., None] * candidates, axis=1)
        agreement = np.linalg.norm(summed, axis=1) / safe_sum
        normal_agreement[start:end] = np.clip(agreement, 0.0, 1.0)
        cov_normal = _normalize(summed)

        # Density-gradient normal: the outward gradient of the same anisotropic
        # field the mesher samples. This is smoother and more surface-aligned
        # than a single splat's shortest axis.
        gradient = np.sum(weights[..., None] * inv_delta, axis=1)
        gradient = _normalize(gradient)
        gradient = _orient(gradient, block_n)

        flat = np.sum(weights * splat_flatness[indices], axis=1) / safe_sum
        flatness[start:end] = np.clip(flat, 0.0, 1.0)

        if normal_source == "MESH":
            chosen = block_n.copy()
        elif normal_source == "COVARIANCE":
            chosen = _orient(cov_normal, block_n)
        elif normal_source == "GRADIENT":
            chosen = gradient
        else:
            # Auto is conservative. Flat, locally agreeing splats carry a real
            # surface normal; moderately anisotropic clouds can use the density
            # gradient; volumetric or disagreeing clouds fall back to the clean
            # mesh instead of baking sampling noise into the normal map.
            chosen = block_n.copy()
            gradient_ok = (flat > 0.15) & (flat <= 0.45)
            covariance_ok = (flat > 0.45) & (normal_agreement[start:end] > 0.35)
            chosen[gradient_ok] = gradient[gradient_ok]
            chosen[covariance_ok] = _orient(cov_normal, block_n)[covariance_ok]

        # Low-evidence texels and degenerate field normals fall back to the
        # clean mesh normal instead of producing a zero tangent-space vector.
        low = weight_sum < 1e-5
        low |= np.linalg.norm(chosen, axis=1) < 1e-6
        chosen[low] = block_n[low]
        chosen = _orient(chosen, block_n)
        strength = float(np.clip(normal_strength, 0.0, 1.0))
        blended = (1.0 - strength) * block_n + strength * chosen
        normal_field[start:end] = _normalize(blended)

    return {
        "colors": np.clip(colors, 0.0, 1.0),
        "normals": normal_field,
        "coverage": np.clip(coverage, 0.0, 1.0),
        "normal_agreement": normal_agreement,
        "flatness": flatness,
        "tangents": tangents,
        "mesh_normals": mesh_normals,
    }


def _orient(normals, reference):
    signs = np.where(np.sum(normals * reference, axis=-1, keepdims=True) < 0.0, -1.0, 1.0)
    return normals * signs


def smooth_normals(normals, valid, sigma_px):
    """Confidence-aware normal smoothing in texture space.

    Nearest-Gaussian gradient normals can carry per-texel jitter even when the
    underlying surface is smooth. A small normalized convolution removes that
    sampling noise without crossing UV gaps or inventing normals in holes.
    """
    if sigma_px <= 0:
        return normals
    weight = valid.astype(np.float32)
    if not weight.any():
        return normals
    filtered = np.zeros_like(normals)
    for channel in range(3):
        filtered[..., channel] = gaussian_filter(
            normals[..., channel] * weight, sigma=float(sigma_px), mode="nearest"
        )
    norm = gaussian_filter(weight, sigma=float(sigma_px), mode="nearest")
    result = filtered / np.maximum(norm[..., None], 1e-6)
    result = _normalize(result)
    result[~valid] = 0.0
    return result


def fill_missing(
    colors,
    normals,
    coverage,
    covered,
    mesh_normals,
    *,
    fill_holes=True,
    fill_background=True,
    margin_px=8,
    coverage_threshold=0.05,
):
    """Fill low-evidence texels from the nearest valid texel and pad UV seams."""
    covered = np.asarray(covered, dtype=bool)
    valid = covered & (coverage > float(coverage_threshold))
    if not valid.any():
        raise ValueError("Projection produced no valid texels; check splat coverage")

    distance, indices = distance_transform_edt(
        ~valid, return_indices=True
    )
    nearest = colors[indices[0], indices[1]]

    filled_holes = (covered & ~valid) if fill_holes else np.zeros_like(covered)
    if fill_background:
        # Dilate the whole texture background, not only a fixed margin. UV
        # poles and tiny island gaps can be farther than the seam margin and
        # would otherwise sample black on the mesh.
        padding = ~covered
    else:
        padding = (
            (~covered) & (distance <= float(margin_px))
            if margin_px > 0
            else np.zeros_like(covered)
        )
    target = filled_holes | padding

    colors = colors.copy()
    normals = normals.copy()
    confidence = coverage.copy()
    colors[target] = nearest[target]
    normals[target] = np.where(
        covered[target, None], mesh_normals[target], np.float32(0.0)
    )
    confidence[target] = 0.0

    return (
        colors,
        normals,
        confidence,
        valid,
        target,
        int(filled_holes.sum()),
        int(padding.sum()),
    )


def encode_normal_map(normals, tangents, mesh_normals, uv_sign):
    """Encode world-space normals in the mesh's tangent basis.

    ``uv_sign`` accounts for mirrored UV islands: the bitangent must follow +V,
    not merely the right-handed cross product.
    """
    normals = np.asarray(normals, dtype=np.float32)
    tangents = np.asarray(tangents, dtype=np.float32)
    mesh_normals = np.asarray(mesh_normals, dtype=np.float32)
    uv_sign = np.asarray(uv_sign, dtype=np.float32)
    bitangents = np.cross(mesh_normals, tangents) * uv_sign[..., None]

    x = np.sum(normals * tangents, axis=-1)
    y = np.sum(normals * bitangents, axis=-1)
    z = np.sum(normals * mesh_normals, axis=-1)

    length = np.sqrt(x * x + y * y + z * z)
    valid = length > 1e-8
    x = np.where(valid, x / np.maximum(length, 1e-8), 0.0)
    y = np.where(valid, y / np.maximum(length, 1e-8), 0.0)
    z = np.where(valid, z / np.maximum(length, 1e-8), 1.0)
    z = np.maximum(z, 1e-6)
    renormalize = np.sqrt(x * x + y * y + z * z)
    x /= np.maximum(renormalize, 1e-8)
    y /= np.maximum(renormalize, 1e-8)
    z /= np.maximum(renormalize, 1e-8)
    return np.clip(np.stack((x * 0.5 + 0.5, y * 0.5 + 0.5, z * 0.5 + 0.5), axis=-1), 0.0, 1.0)


def project(
    mesh,
    splats,
    *,
    texture_size=2048,
    samples=16,
    normal_source="AUTO",
    normal_strength=1.0,
    normal_smoothing=2,
    fill_holes=True,
    fill_background=True,
    margin_px=8,
    coverage_threshold=0.05,
):
    """Run UV rasterization, splat sampling, hole filling, and normal encoding."""
    started = time.perf_counter()
    if normal_source not in NORMAL_SOURCES:
        raise ValueError(f"Unknown normal source: {normal_source}")
    texture_size = int(texture_size)
    samples = int(samples)
    if not 4 <= samples <= 64:
        raise ValueError("Samples must be between 4 and 64")

    prepared = prepare_splats(splats)
    positions, normals, tangents, uv_sign, covered = rasterize_uv(
        mesh["positions"], mesh["normals"], mesh["uvs"], texture_size
    )
    if not covered.any():
        raise ValueError("The UV map covers no texels")

    sampled = sample_surface(
        positions[covered],
        normals[covered],
        tangents[covered],
        prepared,
        samples=samples,
        normal_source=normal_source,
        normal_strength=normal_strength,
    )

    colors = np.zeros((texture_size, texture_size, 3), dtype=np.float32)
    world_normals = np.zeros_like(colors)
    coverage = np.zeros((texture_size, texture_size), dtype=np.float32)
    agreement = np.zeros_like(coverage)
    colors[covered] = sampled["colors"]
    world_normals[covered] = sampled["normals"]
    coverage[covered] = sampled["coverage"]
    agreement[covered] = sampled["normal_agreement"]

    normal_smoothing = float(normal_smoothing)
    if normal_smoothing > 0:
        smooth_valid = covered & (coverage > float(coverage_threshold))
        world_normals = smooth_normals(world_normals, smooth_valid, normal_smoothing)

    (
        colors,
        world_normals,
        confidence,
        valid,
        filled,
        filled_holes,
        padding,
    ) = fill_missing(
        colors,
        world_normals,
        coverage,
        covered,
        normals,
        fill_holes=fill_holes,
        fill_background=fill_background,
        margin_px=margin_px,
        coverage_threshold=coverage_threshold,
    )
    normal_map = encode_normal_map(world_normals, tangents, normals, uv_sign)

    covered_count = int(covered.sum())
    valid_count = int(valid.sum())
    filled_count = int(filled.sum())
    report = {
        "schema": 1,
        "gaussians": int(len(prepared["positions"])),
        "triangles": int(len(mesh["positions"])),
        "texture_size": texture_size,
        "samples": samples,
        "normal_source": normal_source,
        "normal_strength": float(normal_strength),
        "normal_smoothing": normal_smoothing,
        "covered_texels": covered_count,
        "valid_texels": valid_count,
        "filled_texels": filled_count,
        "filled_holes": filled_holes,
        "padding_texels": padding,
        "coverage_mean": float(coverage[covered].mean()) if covered_count else 0.0,
        "normal_agreement_mean": float(agreement[valid].mean()) if valid_count else 0.0,
        "flatness_mean": float(sampled["flatness"].mean()) if len(sampled["flatness"]) else 0.0,
        "elapsed_seconds": float(time.perf_counter() - started),
    }
    return ProjectionResult(
        albedo=colors,
        normal=normal_map,
        confidence=confidence,
        coverage=coverage,
        normal_agreement=agreement,
        covered=covered,
        report=report,
    )


def _view_projection_matrix(view_matrix, projection_matrix):
    view = np.asarray(view_matrix, dtype=np.float64)
    projection = np.asarray(projection_matrix, dtype=np.float64)
    if view.shape == (16,):
        view = view.reshape(4, 4)
    if projection.shape == (16,):
        projection = projection.reshape(4, 4)
    if view.shape != (4, 4) or projection.shape != (4, 4):
        raise ValueError("Camera matrices must be 4×4 or 16-element arrays")
    return projection @ view


def _rasterize_depth(positions, matrix, width, height):
    """Rasterize the target mesh into a camera-space depth buffer."""
    depth = np.full((height, width), np.inf, dtype=np.float32)
    homogeneous = np.concatenate(
        (positions, np.ones(positions.shape[:-1] + (1,), dtype=np.float32)), axis=-1
    )
    clip = homogeneous @ matrix.T
    w = clip[..., 3]
    valid = np.all(w > 1e-6, axis=1)
    ndc = clip[..., :3] / np.maximum(w[..., None], 1e-8)
    screen_x = (ndc[..., 0] * 0.5 + 0.5) * width
    screen_y = (1.0 - (ndc[..., 1] * 0.5 + 0.5)) * height
    z = ndc[..., 2]
    eps = 1e-6

    for index in np.nonzero(valid)[0]:
        x = screen_x[index]
        y = screen_y[index]
        xmin = max(0, int(math.floor(float(x.min()))) - 1)
        xmax = min(width - 1, int(math.ceil(float(x.max()))) + 1)
        ymin = max(0, int(math.floor(float(y.min()))) - 1)
        ymax = min(height - 1, int(math.ceil(float(y.max()))) + 1)
        if xmin > xmax or ymin > ymax:
            continue

        x0, y0 = float(x[0]), float(y[0])
        x1, y1 = float(x[1]), float(y[1])
        x2, y2 = float(x[2]), float(y[2])
        denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denominator) < 1e-12:
            continue
        xs = np.arange(xmin, xmax + 1, dtype=np.float32) + 0.5
        for band_start in range(ymin, ymax + 1, 256):
            band_end = min(ymax, band_start + 255)
            ys = np.arange(band_start, band_end + 1, dtype=np.float32) + 0.5
            grid_x, grid_y = np.meshgrid(xs, ys)
            w0 = ((y1 - y2) * (grid_x - x2) + (x2 - x1) * (grid_y - y2)) / denominator
            w1 = ((y2 - y0) * (grid_x - x2) + (x0 - x2) * (grid_y - y2)) / denominator
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -eps) & (w1 >= -eps) & (w2 >= -eps)
            if not inside.any():
                continue
            bary = np.stack((w0, w1, w2), axis=-1)
            z_value = np.einsum("...k,k->...", bary, z[index])
            yy, xx = np.nonzero(inside)
            gy = yy + band_start
            gx = xx + xmin
            depth[gy, gx] = np.minimum(depth[gy, gx], z_value[inside])
    return depth


def _sample_view(image, x, y):
    height, width = image.shape[:2]
    x = np.clip(x, 0.0, width - 1.0)
    y = np.clip(y, 0.0, height - 1.0)
    coordinates = np.stack((y, x))
    return np.stack(
        [
            map_coordinates(
                image[..., channel],
                coordinates,
                order=1,
                mode="constant",
                cval=0.0,
                prefilter=False,
            )
            for channel in range(4)
        ],
        axis=-1,
    )


def _polygon_mask(width, height, polygon):
    """Rasterize a normalized lasso polygon (bottom-left origin) to a mask."""
    from PIL import Image, ImageDraw, ImageFilter

    mask = Image.new("L", (width, height), 0)
    points = [
        (float(x) * width, (1.0 - float(y)) * height)
        for x, y in polygon
    ]
    ImageDraw.Draw(mask).polygon(points, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    return np.asarray(mask, dtype=np.float32) / 255.0


def project_from_view(
    mesh,
    view_image,
    view_matrix,
    projection_matrix,
    *,
    texture_size=2048,
    alpha_threshold=0.01,
    depth_epsilon=1e-4,
    view_mask=None,
):
    """Project a rendered camera view onto the UV-mapped mesh.

    The view image is rendered from the same camera as the supplied matrices.
    Each UV texel is projected to screen space, depth-tested against the target
    mesh, and samples the splat view where that texel is the visible surface.
    An optional screen-space lasso mask limits the projection to a drawn area.
    """
    started = time.perf_counter()
    image = np.asarray(view_image, dtype=np.float32)
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError("View image must be H×W×3 or H×W×4")
    if image.shape[2] == 3:
        image = np.concatenate(
            (image, np.ones(image.shape[:2] + (1,), dtype=np.float32)), axis=-1
        )
    image = np.clip(image, 0.0, 1.0)
    height, width = image.shape[:2]
    if width < 16 or height < 16:
        raise ValueError("View image is too small")
    texture_size = int(texture_size)
    matrix = _view_projection_matrix(view_matrix, projection_matrix)

    area_mask = None
    if view_mask is not None and len(view_mask) >= 3:
        area_mask = _polygon_mask(width, height, view_mask)
        image[..., 3] *= area_mask

    positions, normals, _tangents, _sign, covered = rasterize_uv(
        mesh["positions"], mesh["normals"], mesh["uvs"], texture_size
    )
    depth_buffer = _rasterize_depth(mesh["positions"], matrix, width, height)

    homogeneous = np.concatenate(
        (positions, np.ones(positions.shape[:-1] + (1,), dtype=np.float32)), axis=-1
    )
    clip = homogeneous @ matrix.T
    w = clip[..., 3]
    ndc = clip[..., :3] / np.maximum(w[..., None], 1e-8)
    screen_x = (ndc[..., 0] * 0.5 + 0.5) * width
    screen_y = (1.0 - (ndc[..., 1] * 0.5 + 0.5)) * height
    depth = ndc[..., 2]

    inside = (
        covered
        & (w > 1e-6)
        & (screen_x >= 0.0)
        & (screen_x < width)
        & (screen_y >= 0.0)
        & (screen_y < height)
    )
    pixel_x = np.clip(np.floor(screen_x).astype(np.int32), 0, width - 1)
    pixel_y = np.clip(np.floor(screen_y).astype(np.int32), 0, height - 1)
    buffer_depth = depth_buffer[pixel_y, pixel_x]
    visible = inside & np.isfinite(buffer_depth) & (depth <= buffer_depth + float(depth_epsilon))

    # Reject back surfaces even when the depth buffer has a hole at a
    # silhouette. The camera position is a good approximation for both
    # perspective and the narrow-angle orthographic case.
    camera_position = np.linalg.inv(np.asarray(view_matrix, dtype=np.float64).reshape(4, 4))[:3, 3]
    view_direction = camera_position[None, :] - positions.reshape(-1, 3)
    view_direction = _normalize(view_direction).reshape(positions.shape)
    facing = np.sum(normals * view_direction, axis=-1) > 0.0
    visible &= facing

    layer = np.zeros((texture_size, texture_size, 4), dtype=np.float32)
    if visible.any():
        sampled = _sample_view(image, screen_x[visible], screen_y[visible])
        alpha = sampled[:, 3]
        keep = alpha >= float(alpha_threshold)
        indices = np.nonzero(visible)
        if keep.any():
            layer[indices[0][keep], indices[1][keep], :3] = sampled[keep, :3]
            layer[indices[0][keep], indices[1][keep], 3] = alpha[keep]

    visible_count = int(np.count_nonzero(layer[..., 3]))
    covered_count = int(covered.sum())
    if visible_count == 0:
        raise ValueError("View projection produced no visible texels; check the camera and splat visibility")
    report = {
        "schema": 1,
        "mode": "view",
        "texture_size": texture_size,
        "view_width": width,
        "view_height": height,
        "covered_texels": covered_count,
        "visible_texels": visible_count,
        "alpha_mean": float(layer[..., 3].mean()),
        "area_mask": area_mask is not None,
        "area_mask_mean": float(area_mask.mean()) if area_mask is not None else 1.0,
        "alpha_threshold": float(alpha_threshold),
        "elapsed_seconds": float(time.perf_counter() - started),
    }
    return ViewProjectionResult(layer=layer, report=report)


def save_view_layer(result, output):
    from PIL import Image

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "layer.png"
    values = np.clip(result.layer * 255.0 + 0.5, 0, 255).astype(np.uint8)
    Image.fromarray(values, mode="RGBA").save(path)
    return str(path)


def save_textures(result, output):
    """Write albedo, tangent-space normal, and confidence PNGs."""
    from PIL import Image

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)

    def _u8(values):
        return np.clip(values * 255.0 + 0.5, 0, 255).astype(np.uint8)

    albedo = output / "albedo.png"
    normal = output / "normal.png"
    confidence = output / "confidence.png"
    Image.fromarray(_u8(result.albedo), mode="RGB").save(albedo)
    Image.fromarray(_u8(result.normal), mode="RGB").save(normal)
    Image.fromarray(_u8(result.confidence), mode="L").save(confidence)
    return {
        "albedo": str(albedo),
        "normal": str(normal),
        "confidence": str(confidence),
    }
