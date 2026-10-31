"""Optional compiled, multicore CPU tracer. Imported only by external Python.

Each task owns scratch space and traces independent rays against the shared
read-only tree. Keep float64, the original support/step rules, and ten bisections;
neither fast-math nor a nearest-neighbor/candidate-count quality cap is used.
Workers use the canonical integrations.splat_project module name for JIT caches.
"""
import math
import numpy as np
import numba
from numba import njit, prange


@njit(cache=True)
def _box(ox, oy, oz, dx, dy, dz, length, bounds, node):
    near, far = 0.0, length
    for axis in range(3):
        origin = ox if axis == 0 else (oy if axis == 1 else oz)
        direction = dx if axis == 0 else (dy if axis == 1 else dz)
        low, high = bounds[node, axis], bounds[node, axis + 3]
        if abs(direction) < 1e-15:
            if origin < low or origin > high:
                return False
        else:
            first, second = (low - origin) / direction, (high - origin) / direction
            near = max(near, min(first, second))
            far = min(far, max(first, second))
            if near > far:
                return False
    return near <= far


@njit(cache=True)
def _field(t, count, coefficients, ids, opacity, sigma2):
    density = 0.0
    for index in range(count):
        delta = t - coefficients[index, 1]
        quad = coefficients[index, 0] * delta * delta + coefficients[index, 2]
        if quad <= sigma2:
            density += opacity[ids[index]] * math.exp(-0.5 * quad)
    return density


@njit(cache=True)
def _ray(ox, oy, oz, dx, dy, dz, length, bounds, escapes, spans, indices,
         centers, precision, opacity, colors, sigma2, epsilon, threshold, max_steps,
         coefficients, ids):
    count, node = 0, 0
    first, last = math.inf, 0.0
    while node < len(bounds):
        if not _box(ox, oy, oz, dx, dy, dz, length, bounds, node):
            node = escapes[node]
            continue
        for offset in range(spans[node, 0], spans[node, 1]):
            gid = indices[offset]
            x, y, z = ox - centers[gid, 0], oy - centers[gid, 1], oz - centers[gid, 2]
            p = precision[gid]
            lx = p[0, 0]*x + p[0, 1]*y + p[0, 2]*z
            ly = p[1, 0]*x + p[1, 1]*y + p[1, 2]*z
            lz = p[2, 0]*x + p[2, 1]*y + p[2, 2]*z
            vx = p[0, 0]*dx + p[0, 1]*dy + p[0, 2]*dz
            vy = p[1, 0]*dx + p[1, 1]*dy + p[1, 2]*dz
            vz = p[2, 0]*dx + p[2, 1]*dy + p[2, 2]*dz
            a = vx*vx + vy*vy + vz*vz
            peak = -(lx*vx + ly*vy + lz*vz) / a
            px, py, pz = lx + peak*vx, ly + peak*vy, lz + peak*vz
            qmin = px*px + py*py + pz*pz
            if qmin >= sigma2:
                continue
            half = math.sqrt((sigma2 - qmin) / a)
            enter, leave = max(peak - half, 0.0), min(peak + half, length)
            if enter >= leave:
                continue
            ids[count] = gid
            coefficients[count, 0] = a
            coefficients[count, 1] = peak
            coefficients[count, 2] = qmin
            coefficients[count, 3] = enter
            coefficients[count, 4] = leave
            coefficients[count, 5] = min(0.5 / math.sqrt(a), (leave - enter) * 0.25)
            count += 1
            first, last = min(first, enter), max(last, leave)
        node += 1
    if count == 0:
        return 0, 0.0, 0.0, 0.0, 0.0, -dx, -dy, -dz, 0.0
    t, previous = first, first
    low, high, hit, running = first, first, False, True
    for iteration in range(max_steps):
        field = _field(t, count, coefficients, ids, opacity, sigma2)
        if iteration == 0 and field >= threshold:
            return 2, 0.0, 0.0, 0.0, 0.0, -dx, -dy, -dz, 0.0
        if field >= threshold:
            low, high, hit = previous, t, True
            break
        if t >= last - epsilon:
            running = False
            break
        step, event = math.inf, math.inf
        for index in range(count):
            peak = coefficients[index, 1]
            enter, leave = coefficients[index, 3], coefficients[index, 4]
            if enter <= t + epsilon and leave > t + epsilon:
                step = min(step, coefficients[index, 5])
            if enter > t + epsilon:
                event = min(event, enter)
            if peak > t + epsilon:
                event = min(event, peak)
        next_t = min(t + max(step, epsilon), event, last)
        previous, t = t, max(next_t, t + epsilon)
    if not hit:
        return (3 if running else 0), 0.0, 0.0, 0.0, 0.0, -dx, -dy, -dz, 0.0
    for _ in range(10):
        middle = (low + high) * 0.5
        if _field(middle, count, coefficients, ids, opacity, sigma2) >= threshold:
            high = middle
        else:
            low = middle
    t = (low + high) * 0.5
    red, green, blue, gx, gy, gz, total, magnitude_sum = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    for index in range(count):
        delta = t - coefficients[index, 1]
        quad = coefficients[index, 0]*delta*delta + coefficients[index, 2]
        if quad > sigma2:
            continue
        gid = ids[index]
        weight = opacity[gid] * math.exp(-0.5 * quad)
        total += weight
        red += weight * colors[gid, 0]
        green += weight * colors[gid, 1]
        blue += weight * colors[gid, 2]
        x = ox + t*dx - centers[gid, 0]
        y = oy + t*dy - centers[gid, 1]
        z = oz + t*dz - centers[gid, 2]
        p = precision[gid]
        lx = p[0, 0]*x + p[0, 1]*y + p[0, 2]*z
        ly = p[1, 0]*x + p[1, 1]*y + p[1, 2]*z
        lz = p[2, 0]*x + p[2, 1]*y + p[2, 2]*z
        nx = p[0, 0]*lx + p[1, 0]*ly + p[2, 0]*lz
        ny = p[0, 1]*lx + p[1, 1]*ly + p[2, 1]*lz
        nz = p[0, 2]*lx + p[1, 2]*ly + p[2, 2]*lz
        gx, gy, gz = gx + weight*nx, gy + weight*ny, gz + weight*nz
        magnitude_sum += weight * math.sqrt(nx*nx + ny*ny + nz*nz)
    magnitude = math.sqrt(gx*gx + gy*gy + gz*gz)
    nx, ny, nz, agreement = -dx, -dy, -dz, 0.0
    if magnitude > 1e-10:
        denominator = max(magnitude, 1e-8)
        nx, ny, nz = gx / denominator, gy / denominator, gz / denominator
        if nx*dx + ny*dy + nz*dz > 0:
            nx, ny, nz = -nx, -ny, -nz
        agreement = min(1.0, max(0.0, magnitude / max(magnitude_sum, 1e-20)))
    total = max(total, 1e-20)
    return 1, t, red/total, green/total, blue/total, nx, ny, nz, agreement


@njit(cache=True, parallel=True, nogil=True)
def _trace(origins, directions, lengths, bounds, escapes, spans, indices, centers,
           precision, opacity, colors, sigma2, epsilon, density, max_steps, tasks):
    count = len(origins)
    flags = np.zeros(count, np.int8)
    values = np.zeros((count, 8), np.float64)
    for task in prange(tasks):
        # All source Gaussians fit: never drop candidates on complex rays.
        coefficients = np.empty((len(centers), 6), np.float64)
        ids = np.empty(len(centers), np.int32)
        start, end = count * task // tasks, count * (task + 1) // tasks
        for ray in range(start, end):
            result = _ray(origins[ray, 0], origins[ray, 1], origins[ray, 2],
                          directions[ray, 0], directions[ray, 1], directions[ray, 2], lengths[ray],
                          bounds, escapes, spans, indices, centers, precision, opacity, colors,
                          sigma2, epsilon, density, max_steps, coefficients, ids)
            flags[ray] = result[0]
            values[ray, 0], values[ray, 1], values[ray, 2], values[ray, 3] = result[1], result[2], result[3], result[4]
            values[ray, 4], values[ray, 5], values[ray, 6], values[ray, 7] = result[5], result[6], result[7], result[8]
    return flags, values


def flatten_tree(tree):
    """Stackless DFS layout in the same leaf order as the NumPy reference."""
    bounds, escapes, spans, indices = [], [], [], []

    def visit(node_id):
        node = tree.nodes[node_id]
        index = len(bounds)
        bounds.append(np.r_[node.low, node.high])
        escapes.append(0)
        start = len(indices)
        if node.indices is not None:
            indices.extend(node.indices)
        spans.append((start, len(indices)))
        for child in reversed(node.children):
            visit(child)
        escapes[index] = len(bounds)

    visit(0)
    return (np.asarray(bounds, np.float64), np.asarray(escapes, np.int32),
            np.asarray(spans, np.int32), np.asarray(indices, np.int32))


def trace_native(tree, origins, directions, lengths, *, density, max_steps,
                 progress=None, chunk=32768):
    arrays = flatten_tree(tree)
    # Keep scratch bounded to 128 MiB and leave CPU capacity for Blender.
    bytes_per_task = max(1, len(tree.positions)) * (6 * 8 + 4)
    previous_threads = numba.get_num_threads()
    threads = max(1, min(8, previous_threads, (128 * 1024**2) // bytes_per_task))
    numba.set_num_threads(threads)
    tree.trace_info = {"tracer_backend": "NUMBA_CPU", "tracer_threads": threads,
                       "tracer_scratch_bytes": threads * bytes_per_task}
    flags, values = [], []
    try:
        if progress:
            progress(0, len(origins))
        for start in range(0, len(origins), chunk):
            tree.cancelled()
            end = min(start + chunk, len(origins))
            f, v = _trace(np.ascontiguousarray(origins[start:end]), np.ascontiguousarray(directions[start:end]),
                          np.ascontiguousarray(lengths[start:end]), *arrays, tree.positions, tree.precision_root,
                          tree.opacity, tree.colors, tree.sigma**2,
                          max(tree.diagonal * 1e-10, np.finfo(float).eps), density, max_steps,
                          min(threads, end - start))
            flags.append(f)
            values.append(v)
            if progress:
                progress(end, len(origins))
    finally:
        numba.set_num_threads(previous_threads)
    tree.cancelled()
    flags, values = np.concatenate(flags), np.concatenate(values)
    return {"hit": flags == 1, "clipped": flags == 2, "step_limit": flags == 3,
            "distance": values[:, 0], "colors": values[:, 1:4].astype(np.float32),
            "normals": values[:, 4:7].astype(np.float32),
            "normal_agreement": values[:, 7].astype(np.float32)}
