"""Compiled source-local Gaussian alpha compositor (external Python only)."""
import math

import numba
from numba import njit, prange
import numpy as np

from .octree_native import _box, flatten_tree


@njit(cache=True, parallel=True, nogil=True)
def _render(origins, directions, half_length, max_gap, layer_width, bounds, escapes,
            spans, indices, centers, precision, opacity, colors, tasks):
    # RGB, confidence, expected depth, depth variance, anchor gap, reason.
    result = np.zeros((len(origins), 8), np.float64)
    for task in prange(tasks):
        depths = np.empty(len(centers), np.float64)
        alphas = np.empty(len(centers), np.float64)
        ids = np.empty(len(centers), np.int32)
        for ray in range(len(origins)*task//tasks, len(origins)*(task+1)//tasks):
            o, d = origins[ray], directions[ray]
            count, node = 0, 0
            while node < len(bounds):
                if not _box(o[0], o[1], o[2], d[0], d[1], d[2], 2*half_length, bounds, node):
                    node = escapes[node]
                    continue
                for offset in range(spans[node, 0], spans[node, 1]):
                    gid = indices[offset]
                    x, y, z = o[0]-centers[gid, 0], o[1]-centers[gid, 1], o[2]-centers[gid, 2]
                    p = precision[gid]
                    lx = p[0, 0]*x + p[0, 1]*y + p[0, 2]*z
                    ly = p[1, 0]*x + p[1, 1]*y + p[1, 2]*z
                    lz = p[2, 0]*x + p[2, 1]*y + p[2, 2]*z
                    vx = p[0, 0]*d[0] + p[0, 1]*d[1] + p[0, 2]*d[2]
                    vy = p[1, 0]*d[0] + p[1, 1]*d[1] + p[1, 2]*d[2]
                    vz = p[2, 0]*d[0] + p[2, 1]*d[1] + p[2, 2]*d[2]
                    a = vx*vx + vy*vy + vz*vz
                    t = -(lx*vx + ly*vy + lz*vz) / a
                    if t < 0 or t > 2*half_length:
                        continue
                    px, py, pz = lx+t*vx, ly+t*vy, lz+t*vz
                    q = px*px + py*py + pz*pz
                    if q > 12.25:
                        continue
                    alpha = opacity[gid]*math.exp(-0.5*q)
                    if alpha < 1/255:
                        continue
                    depths[count], alphas[count], ids[count] = t, min(alpha, 0.999), gid
                    count += 1
                node += 1
            if not count:
                continue
            anchor, nearest = half_length, math.inf
            for reliable_only in range(2):
                for i in range(count):
                    if reliable_only == 0 and alphas[i] < 0.02:
                        continue
                    gap = abs(depths[i] - half_length)
                    if gap < nearest:
                        nearest, anchor = gap, depths[i]
                if nearest < math.inf:
                    break
            result[ray, 6] = nearest
            if nearest > max_gap:
                result[ray, 7] = 2
                continue
            order = np.argsort(depths[:count], kind="mergesort")
            transmittance, total, red, green, blue, depth, second = 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            for index in order:
                if abs(depths[index] - anchor) > 2*layer_width:
                    continue
                weight = transmittance*alphas[index]
                gid = ids[index]
                red += weight*colors[gid, 0]
                green += weight*colors[gid, 1]
                blue += weight*colors[gid, 2]
                depth += weight*depths[index]
                second += weight*depths[index]*depths[index]
                total += weight
                transmittance *= 1-alphas[index]
                if transmittance < 1e-4:
                    break
            if total < 0.5:
                result[ray, 7] = 3
                continue
            expected = depth/total
            result[ray, 0], result[ray, 1], result[ray, 2] = red/total, green/total, blue/total
            result[ray, 3] = total*math.exp(-0.5*(nearest/(max_gap*0.5))**2)
            result[ray, 4], result[ray, 5] = expected, max(0.0, second/total-expected*expected)
            result[ray, 7] = 1
    return result


def render_native(tree, origins, directions, half_length, max_gap, layer_width, progress=None):
    previous_threads = numba.get_num_threads()
    per_task = len(tree.positions)*52  # Includes worst-case sort workspace.
    threads = max(1, min(8, previous_threads, (128*1024**2)//max(1, per_task)))
    arrays = flatten_tree(tree)
    tree.trace_info = {"tracer_backend": "NUMBA_CPU", "tracer_threads": threads,
                       "tracer_scratch_bytes": threads*per_task}
    values = []
    numba.set_num_threads(threads)
    try:
        if progress:
            progress(0, len(origins))
        for start in range(0, len(origins), 32768):
            tree.cancelled()
            end = min(start+32768, len(origins))
            values.append(_render(np.ascontiguousarray(origins[start:end]), np.ascontiguousarray(directions[start:end]),
                                  half_length, max_gap, layer_width, *arrays, tree.positions, tree.precision_root,
                                  tree.opacity, tree.colors, min(threads, end-start)))
            if progress:
                progress(end, len(origins))
    finally:
        numba.set_num_threads(previous_threads)
    tree.cancelled()
    return np.concatenate(values)
