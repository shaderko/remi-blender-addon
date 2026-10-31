"""Experimental closest-layer Gaussian alpha compositing, never the production button.

Compare on real saved-scene snapshots before choosing a replacement bake model.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import time

import numba
from numba import njit, prange
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.splat_project.octree import GaussianOctree
from integrations.splat_project.octree_native import _box, flatten_tree
from integrations.splat_project.projection import finish_projection, rasterize_uv, save_textures


@njit(cache=True, parallel=True, nogil=True)
def render_rays(origins, directions, target_depth, search_distance, bandwidth, use_mode,
                bounds, escapes, spans, indices, centers, precision, opacity, colors, tasks):
    result = np.zeros((len(origins), 5))
    for task in prange(tasks):
        depths = np.empty(len(centers))
        alphas = np.empty(len(centers))
        ids = np.empty(len(centers), np.int32)
        for ray in range(len(origins) * task // tasks, len(origins) * (task + 1) // tasks):
            o, d = origins[ray], directions[ray]
            count, node = 0, 0
            while node < len(bounds):
                if not _box(o[0], o[1], o[2], d[0], d[1], d[2], 2*search_distance, bounds, node):
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
                    if t < 0 or t > 2*search_distance:
                        continue
                    px, py, pz = lx+t*vx, ly+t*vy, lz+t*vz
                    quad = px*px + py*py + pz*pz
                    if quad > 12.25:
                        continue
                    alpha = opacity[gid] * math.exp(-0.5*quad)
                    if alpha < 1/255:
                        continue
                    depths[count], alphas[count], ids[count] = t, min(alpha, 0.999), gid
                    count += 1
                node += 1
            if not count:
                continue
            center = target_depth
            if use_mode:
                for iteration in range(8):
                    mass, moment = 0.0, 0.0
                    for i in range(count):
                        delta = (depths[i] - center) / bandwidth
                        w = alphas[i] * math.exp(-0.5*delta*delta)
                        mass, moment = mass + w, moment + w*depths[i]
                    if mass < 1e-4:
                        break
                    next_center = moment/mass
                    center = min(max(next_center, target_depth-search_distance), target_depth+search_distance)
            else:
                nearest = math.inf
                for i in range(count):
                    distance = abs(depths[i] - target_depth)
                    if distance < nearest and alphas[i] > 0.02:
                        nearest, center = distance, depths[i]
            order = np.argsort(depths[:count])
            transmittance, total, red, green, blue, depth = 1.0, 0.0, 0.0, 0.0, 0.0, 0.0
            for index in order:
                if abs(depths[index] - center) > bandwidth*2:
                    continue
                w = transmittance * alphas[index]
                gid = ids[index]
                red += w*colors[gid, 0]
                green += w*colors[gid, 1]
                blue += w*colors[gid, 2]
                depth += w*depths[index]
                total += w
                transmittance *= 1-alphas[index]
                if transmittance < 1e-4:
                    break
            if total > 0.5:
                result[ray, 0], result[ray, 1], result[ray, 2] = red/total, green/total, blue/total
                result[ray, 3], result[ray, 4] = total, depth/total
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--bandwidth", type=float, default=0.005)
    parser.add_argument("--nearest", action="store_true")
    parser.add_argument("--smooth-rays", action="store_true", help="Use geometric vertex normals for ray directions, not split shading normals")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Prototype output must be empty")
    args.output.mkdir(parents=True, exist_ok=True)
    mesh = dict(np.load(args.snapshot / "mesh.npz", allow_pickle=False))
    tree = GaussianOctree(dict(np.load(args.snapshot / "source.npz", allow_pickle=False)))
    p, n, tangents, sign, covered = rasterize_uv(mesh["positions"], mesh["normals"], mesh["uvs"], args.size,
                                              corner_tangents=mesh.get("tangents"), tangent_signs=mesh.get("tangent_signs"))
    ray_normals = n
    if args.smooth_rays:
        positions = mesh["positions"]
        unique, inverse = np.unique(positions.reshape(-1, 3), axis=0, return_inverse=True)
        face = np.cross(positions[:, 1] - positions[:, 0], positions[:, 2] - positions[:, 0])
        summed = np.zeros_like(unique)
        np.add.at(summed, inverse, np.repeat(face, 3, axis=0))
        summed /= np.maximum(np.linalg.norm(summed, axis=1, keepdims=True), 1e-20)
        geometric = summed[inverse].reshape(positions.shape)
        _p, ray_normals, _t, _s, _c = rasterize_uv(positions, geometric, mesh["uvs"], args.size)
    distance, bandwidth = tree.diagonal*0.1, tree.diagonal*args.bandwidth
    points, normals = p[covered].astype(float), ray_normals[covered].astype(float)
    numba.set_num_threads(8)
    started = time.perf_counter()
    data = render_rays(points + normals*distance, -normals, distance, distance, bandwidth, not args.nearest,
                       *flatten_tree(tree), tree.positions, tree.precision_root, tree.opacity, tree.colors, 8)
    print("RADIANCE_PROTOTYPE", len(points), "rays", time.perf_counter()-started, "seconds", flush=True)
    sampled = {"colors": data[:, :3].astype(np.float32), "normals": n[covered],
               "coverage": data[:, 3].astype(np.float32), "normal_agreement": (data[:, 3] > 0).astype(np.float32)}
    result = finish_projection(mesh, n, tangents, sign, covered, sampled, texture_size=args.size,
                               normal_smoothing=0, fill_holes=False, fill_background=True,
                               report={"method": "LOCAL_RADIANCE_PROTOTYPE", "normal_source": "NEUTRAL_FOR_COLOR_TEST",
                                       "bandwidth_fraction": args.bandwidth, "nearest": args.nearest,
                                       "smooth_rays": args.smooth_rays,
                                       "valid_rays": int((data[:, 3] > .5).sum())})
    save_textures(result, args.output)
    np.savez_compressed(args.output / "samples.npz", radiance=data, covered=covered)
    (args.output / "report.json").write_text(json.dumps(result.report, indent=2))


if __name__ == "__main__":
    main()
