"""Correspondence-aware local Gaussian appearance, not a density isosurface.

Use the source layer nearest each target texel, then alpha-composite only that
layer. Foreground layers elsewhere in the cage cannot cover the intended part.
Missing/ambiguous source evidence stays missing; an octree cannot invent it.
"""
import time

import numpy as np
from scipy.ndimage import distance_transform_edt, find_objects, gaussian_filter, label

from .octree import GaussianOctree, _SplitBatch
from .projection import ProjectionResult, _normalize, encode_normal_map, rasterize_uv


def geometric_corner_normals(positions, vertex_indices=None, orientation_sign=1.0):
    positions = np.asarray(positions, dtype=np.float32)
    if vertex_indices is None:
        _unique, inverse = np.unique(positions.reshape(-1, 3), axis=0, return_inverse=True)
    else:
        _unique, inverse = np.unique(vertex_indices, return_inverse=True)
    inverse = inverse.reshape(-1)
    summed = np.zeros((int(inverse.max())+1, 3), dtype=np.float64)
    face = np.cross(positions[:, 1]-positions[:, 0], positions[:, 2]-positions[:, 0])
    np.add.at(summed, inverse, np.repeat(face, 3, axis=0))
    normals = _normalize(summed)[inverse].reshape(positions.shape)
    return (normals*orientation_sign).astype(np.float32)


def _composite(depths, alpha, colors, target, max_gap, width):
    """NumPy reference for one ray; RGB remains in the model/native RGB convention."""
    result = np.zeros(8, dtype=float)
    if not len(depths):
        return result
    reliable = np.flatnonzero(alpha >= 0.02)
    if not len(reliable):
        reliable = np.arange(len(depths))
    seed = reliable[np.argmin(np.abs(depths[reliable]-target))]
    gap, anchor = abs(depths[seed]-target), depths[seed]
    result[6] = gap
    if gap > max_gap:
        result[7] = 2
        return result
    selected = np.flatnonzero(np.abs(depths-anchor) <= 2*width)
    selected = selected[np.argsort(depths[selected], kind="stable")]
    a = np.minimum(alpha[selected], 0.999)
    transmittance = np.r_[1.0, np.cumprod(1-a)]
    weights = transmittance[:-1]*a
    # Match the native early-termination threshold, without dropping candidates beforehand.
    crossing = np.flatnonzero(transmittance[1:] < 1e-4)
    if len(crossing):
        weights[crossing[0]+1:] = 0
    total = float(weights.sum())
    if total < 0.5:
        result[7] = 3
        return result
    expected = float(np.sum(weights*depths[selected])/total)
    result[:3] = np.sum(weights[:, None]*colors[selected], axis=0)/total
    result[3] = total*np.exp(-0.5*(gap/(max_gap*0.5))**2)
    result[4], result[5] = expected, max(0, float(np.sum(weights*depths[selected]**2)/total-expected**2))
    result[7] = 1
    return result


def render_reference(tree, origins, directions, half_length, max_gap, layer_width, progress=None, fallback=None):
    output = np.zeros((len(origins), 8))

    def packet(start, end):
        tree.cancelled()
        try:
            pairs = tree._pairs(origins[start:end], directions[start:end], np.full(end-start, 2*half_length), 262144)
        except _SplitBatch:
            if end-start == 1:
                raise ValueError("Local Gaussian ray exceeds the candidate memory budget")
            middle = (start+end)//2
            packet(start, middle)
            packet(middle, end)
            return
        if pairs is None:
            return
        rid, gid, _a, peak, qmin, _enter, _leave = pairs
        alpha = tree.opacity[gid]*np.exp(-0.5*qmin)
        keep = (peak >= 0) & (peak <= 2*half_length) & (alpha >= 1/255)
        rid, gid, peak, alpha = rid[keep], gid[keep], peak[keep], alpha[keep]
        order = np.argsort(rid, kind="stable")
        rid, gid, peak, alpha = rid[order], gid[order], peak[order], alpha[order]
        unique, starts = np.unique(rid, return_index=True)
        stops = np.r_[starts[1:], len(rid)]
        for ray, first, last in zip(unique, starts, stops):
            output[start+ray] = _composite(peak[first:last], alpha[first:last], tree.colors[gid[first:last]],
                                            half_length, max_gap, layer_width)

    tree.trace_info = {"tracer_backend": "NUMPY", "tracer_threads": 1}
    if fallback:
        tree.trace_info["acceleration_fallback"] = fallback
    for start in range(0, len(origins), 1024):
        end = min(start+1024, len(origins))
        packet(start, end)
        if progress:
            progress(end, len(origins))
    return output


def render_local(tree, points, normals, max_gap, layer_width, *, backend="AUTO", progress=None):
    if backend not in {"AUTO", "NUMPY", "NUMBA_CPU"}:
        raise ValueError("Unknown local Gaussian backend")
    points, normals = np.asarray(points, float), np.asarray(normals, float)
    if (points.ndim != 2 or points.shape[1:] != (3,) or normals.shape != points.shape or not len(points)
            or not np.isfinite(points).all() or not np.isfinite(normals).all()
            or not np.allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-5)
            or not np.isfinite(max_gap) or max_gap <= 0 or not np.isfinite(layer_width) or layer_width <= 0):
        raise ValueError("Expected finite points, unit ray normals, and positive matching distances")
    half_length = max_gap+2*layer_width
    origins, directions = points+normals*half_length, -normals
    # Spatial packets preserve locality without changing individual correspondences.
    cell = np.clip((points-tree.low)/np.maximum(tree.high-tree.low, 1e-30)*1023, 0, 1023).astype(np.uint64)
    morton = np.zeros(len(points), np.uint64)
    for bit in range(10):
        for axis in range(3):
            morton |= ((cell[:, axis] >> bit) & 1) << (3*bit+axis)
    order = np.argsort(morton, kind="stable")
    origins, directions = origins[order], directions[order]
    fallback = None
    if backend != "NUMPY":
        try:
            from .local_gaussian_native import render_native
            data = render_native(tree, origins, directions, half_length, max_gap, layer_width, progress)
        except (InterruptedError, MemoryError):
            raise
        except Exception as exc:
            if backend != "AUTO":
                raise
            fallback = f"{type(exc).__name__}: {exc}"
            print(f"Parallel local compositor unavailable; using NumPy: {exc}", flush=True)
        else:
            return data[np.argsort(order)], half_length
    data = render_reference(tree, origins, directions, half_length, max_gap, layer_width, progress, fallback)
    return data[np.argsort(order)], half_length


def depth_normals(hit_positions, geometry_normals, confidence, covered, sigma, cancelled):
    """Conservative chart-local plane fits; uncertain source depth cannot create bumps."""
    output = geometry_normals.copy()
    normal_confidence = np.zeros(covered.shape, np.float32)
    charts, _count = label(covered)
    radius = max(1.0, float(sigma))
    halo = int(np.ceil(4*radius))
    for chart, bounds in enumerate(find_objects(charts), 1):
        if bounds is None:
            continue
        ys, xs = bounds
        for start in range(ys.start, ys.stop, 64):
            cancelled()
            first, last = max(ys.start, start-halo), min(ys.stop, start+64+halo)
            region = (slice(first, last), xs)
            mask = (charts[region] == chart) & (confidence[region] >= 0.1)
            if int(mask.sum()) < 9:
                continue
            weight = mask.astype(float)*confidence[region]
            total = gaussian_filter(weight, radius, mode="constant")
            local = hit_positions[region].astype(float)
            # Center locally to avoid loss of precision for translated objects.
            origin = np.mean(local[mask], axis=0)
            local -= origin
            mean = np.stack([gaussian_filter(local[..., i]*weight, radius, mode="constant")
                             for i in range(3)], axis=-1)/np.maximum(total[..., None], 1e-20)
            covariance = np.zeros((*mask.shape, 3, 3))
            for i in range(3):
                for j in range(i, 3):
                    moment = gaussian_filter(local[..., i]*local[..., j]*weight, radius, mode="constant")
                    value = moment/np.maximum(total, 1e-20)-mean[..., i]*mean[..., j]
                    covariance[..., i, j] = covariance[..., j, i] = value
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            normal = eigenvectors[..., :, 0]
            reference = geometry_normals[region]
            dot = np.sum(normal*reference, axis=-1)
            normal *= np.where(dot < 0, -1, 1)[..., None]
            planar = np.maximum(eigenvalues[..., 0], 0)/np.maximum(eigenvalues[..., 1], 1e-20)
            valid = (mask & (total > 0.25) & (planar < 0.05)
                     & (eigenvalues[..., 1] > eigenvalues[..., 2]*0.01) & (np.abs(dot) > 0.70710678))
            keep_rows = np.zeros(mask.shape, bool)
            keep_rows[start-first:min(start+64, ys.stop)-first] = True
            valid &= keep_rows
            strength = ((1-planar[valid]/0.05)*confidence[region][valid]
                        *np.clip((np.abs(dot[valid])-0.70710678)/(1-0.70710678), 0, 1))
            output[region][valid] = _normalize(reference[valid]*(1-strength[:, None])+normal[valid]*strength[:, None])
            normal_confidence[region][valid] = strength
    return output, normal_confidence


def assemble_local(mesh, positions, shader_normals, geometry_normals, tangents, signs, covered,
                   data, half_length, *, normal_mode, normal_strength, normal_smoothing,
                   fill_holes, fill_background, margin_px, cancelled):
    hit = data[:, 7] == 1
    valid = np.zeros(covered.shape, bool)
    valid[covered] = hit
    colors = np.full((*covered.shape, 3), 0.5, np.float32)
    confidence = np.zeros(covered.shape, np.float32)
    colors[covered] = np.where(hit[:, None], data[:, :3], 0.5)
    confidence[covered] = data[:, 3]
    source_positions = positions.astype(float).copy()
    offsets = np.zeros(covered.shape)
    offsets[covered] = np.where(hit, half_length-data[:, 4], 0)
    source_positions += geometry_normals*offsets[..., None]
    detailed, normal_confidence = geometry_normals.copy(), np.zeros(covered.shape, np.float32)
    if normal_mode == "AUTO":
        detailed, normal_confidence = depth_normals(source_positions, geometry_normals, confidence,
                                                   covered, max(1, normal_smoothing), cancelled)
    world = _normalize(shader_normals*(1-normal_strength)+detailed*normal_strength)
    normal_map = encode_normal_map(world, tangents, shader_normals, signs)
    charts, _count = label(covered)
    filled = 0
    if fill_holes:
        # Never borrow colors from a different UV island or fill a large hidden region.
        for chart, bounds in enumerate(find_objects(charts), 1):
            cancelled()
            local_valid = valid[bounds] & (charts[bounds] == chart)
            if not local_valid.any():
                continue
            distance, nearest = distance_transform_edt(~local_valid, return_indices=True)
            missing = ((charts[bounds] == chart) & ~local_valid & (distance <= min(2, max(0, margin_px))))
            colors[bounds][missing] = colors[bounds][nearest[0], nearest[1]][missing]
            filled += int(missing.sum())
    # Pad both encoded normals and color, not world normals in an unrelated island's basis.
    distance, nearest = distance_transform_edt(~covered, return_indices=True)
    padding = ~covered & ((distance <= margin_px) | bool(fill_background))
    colors[padding] = colors[nearest[0], nearest[1]][padding]
    normal_map[padding] = normal_map[nearest[0], nearest[1]][padding]
    return colors, normal_map, confidence, valid, normal_confidence, filled, int(padding.sum())


def project_local(mesh, splats, *, texture_size=2048, match_distance=0.02, layer_width=0.005,
                  normal_mode="AUTO", normal_strength=1.0, normal_smoothing=2, smooth_rays=True,
                  fill_holes=True, fill_background=True, margin_px=8, backend="AUTO",
                  progress=None, cancelled=None):
    started = time.perf_counter()
    if (not np.isfinite(match_distance) or not 0 < match_distance <= 0.25
            or not np.isfinite(layer_width) or not 0 < layer_width <= 0.05
            or not np.isfinite(normal_strength) or not 0 <= normal_strength <= 1
            or normal_mode not in {"AUTO", "MESH"} or not 0 <= normal_smoothing <= 16
            or not 0 <= margin_px <= 256):
        raise ValueError("Invalid local Gaussian bake settings")
    cancelled = cancelled or (lambda: None)
    if progress:
        progress("Building source-local Gaussian octree…")
    tree = GaussianOctree(splats, cancelled=cancelled)
    corner_geometry = (geometric_corner_normals(mesh["positions"], mesh.get("vertex_indices"),
                                               float(mesh.get("orientation_sign", 1.0)))
                       if smooth_rays else mesh["normals"])
    lengths = np.linalg.norm(corner_geometry, axis=-1, keepdims=True)
    corner_geometry = np.where(lengths > 1e-8, corner_geometry, mesh["normals"])
    positions, shader_normals, tangents, signs, covered, geometry = rasterize_uv(
        mesh["positions"], mesh["normals"], mesh["uvs"], texture_size,
        corner_tangents=mesh.get("tangents"), tangent_signs=mesh.get("tangent_signs"),
        corner_ray_normals=corner_geometry)
    if not covered.any():
        raise ValueError("The UV map covers no texels")
    trace_started = time.perf_counter()

    def tick(done, total):
        info = getattr(tree, "trace_info", {})
        rate = done/max(time.perf_counter()-trace_started, 1e-6)
        label = (f"CPU ×{info.get('tracer_threads', 1)}" if info.get("tracer_backend") == "NUMBA_CPU"
                 else "NumPy fallback" if info.get("acceleration_fallback") else "CPU NumPy")
        progress(f"Tracing local layers · {done:,}/{total:,} · {rate:,.0f} rays/s · {label}")

    data, half_length = render_local(tree, positions[covered], geometry[covered],
                                    tree.diagonal*match_distance, tree.diagonal*layer_width,
                                    backend=backend, progress=tick if progress else None)
    cancelled()
    hit = data[:, 7] == 1
    if not hit.any():
        raise ValueError("No matching local Gaussian layer; check source alignment and Maximum Source Gap")
    if progress:
        progress("Estimating stable depth normals and padding UV seams…")
    colors, normal, confidence, valid, normal_confidence, filled, padding = assemble_local(
        mesh, positions, shader_normals, geometry, tangents, signs, covered, data, half_length,
        normal_mode=normal_mode, normal_strength=normal_strength, normal_smoothing=normal_smoothing,
        fill_holes=fill_holes, fill_background=fill_background, margin_px=margin_px, cancelled=cancelled)
    report = {"schema": 1, "method": "OCTREE_LOCAL", "mode": "octree_local", "texture_size": texture_size,
              "gaussians": tree.source_count, "triangles": len(mesh["positions"]), "covered_texels": int(covered.sum()),
              "valid_texels": int(hit.sum()), "rays": len(data), "hits": int(hit.sum()), "misses": int((~hit).sum()),
              "gap_rejected_rays": int((data[:, 7] == 2).sum()), "weak_opacity_rays": int((data[:, 7] == 3).sum()),
              "filled_holes": filled, "padding_texels": padding, "coverage_mean": float(confidence[covered].mean()),
              "normal_source": "CLEAN_GEOMETRY_WITH_CONFIDENT_LOCAL_DEPTH" if normal_mode == "AUTO" else "CLEAN_GEOMETRY",
              "source_normal_texels": int((normal_confidence > 0).sum()), "normal_strength": normal_strength,
              "normal_smoothing": normal_smoothing, "normal_agreement_mean": float(normal_confidence[valid].mean()),
              "match_distance_fraction": match_distance, "layer_width_fraction": layer_width,
              "appearance_colorspace": "model/native RGB (not a PBR albedo decomposition)",
              "compositing": "front-to-back alpha, restricted to the closest local source layer",
              "missing_evidence": "neutral color and clean geometry normal; at most two pixels of chart-local color fill",
              "limitations": "Requires nearby source data; cannot invent hidden appearance, recover missing geometry, or remove baked lighting",
              "octree_nodes": len(tree.nodes), "octree_depth": tree.depth,
              "bounds_low": tree.low.tolist(), "bounds_high": tree.high.tolist(), **tree.trace_info,
              "elapsed_seconds": time.perf_counter()-started}
    return ProjectionResult(colors, normal, confidence, confidence.copy(), normal_confidence, covered, report)
