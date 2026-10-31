"""Compare reference and compiled rays from a frozen projection request; never modify its job."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.splat_project.octree import GaussianOctree
from integrations.splat_project.projection import rasterize_uv
from integrations.splat_project.worker import _load_mesh, _load_splats


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def benchmark(request_path, output, ray_count=8192, seed=326):
    request = json.loads(Path(request_path).read_text())
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Benchmark output must be empty")
    if ray_count < 1:
        raise ValueError("Ray count must be positive")
    mesh, splats = _load_mesh(request["mesh"]), _load_splats(request["archive"])
    tree = GaussianOctree(splats)
    p, n, _t, _s, covered = rasterize_uv(
        mesh["positions"], mesh["normals"], mesh["uvs"], int(request["texture_size"]),
        corner_tangents=mesh.get("tangents"), tangent_signs=mesh.get("tangent_signs"))
    count = min(ray_count, int(covered.sum()))
    selected = np.random.default_rng(seed).choice(int(covered.sum()), count, replace=False)
    points, normals = p[covered][selected], n[covered][selected]
    distance = tree.diagonal * float(request.get("ray_distance", 0.1))
    origins, directions = points + normals * distance, -normals
    settings = {"density": float(request.get("surface_density", 0.5)),
                "max_steps": int(request.get("ray_steps", 512))}

    def trace(backend):
        started = time.perf_counter()
        result = tree.trace(origins, directions, 2 * distance, backend=backend, **settings)
        return result, time.perf_counter() - started

    reference, old_seconds = trace("NUMPY")
    _cold, cold_seconds = trace("NUMBA_CPU")
    native, new_seconds = trace("NUMBA_CPU")
    for key in ("hit", "clipped", "step_limit"):
        np.testing.assert_array_equal(reference[key], native[key], err_msg=key)
    hits = reference["hit"]
    if not hits.any():
        raise ValueError("Sample has no hits for a normal-map comparison")
    normal_vectors = [value["normals"][hits].astype(float) for value in (reference, native)]
    normal_vectors = [value / np.maximum(np.linalg.norm(value, axis=1, keepdims=True), 1e-20)
                      for value in normal_vectors]
    angle = np.degrees(np.arccos(np.clip(np.sum(normal_vectors[0] * normal_vectors[1], axis=1), -1, 1)))
    report = {"schema": 1, "seed": seed, "gaussians": tree.source_count, "sample_rays": count,
              "source_sha256": digest(request["archive"]), "mesh_sha256": digest(request["mesh"]),
              "trace_settings": settings, "reference_seconds": old_seconds,
              "compiled_first_call_seconds": cold_seconds, "compiled_warm_seconds": new_seconds,
              "warm_speedup": old_seconds / new_seconds, "warm_rays_per_second": count / new_seconds,
              "matching_hit_clipped_step_flags": True,
              "max_distance_error": float(np.max(np.abs(reference["distance"][hits] - native["distance"][hits]))),
              "max_color_error": float(np.max(np.abs(reference["colors"][hits] - native["colors"][hits]))),
              "normal_angle_degrees_median_p99_max": np.percentile(angle, [50, 99, 100]).tolist(),
              **tree.trace_info,
              "limitation": "One-machine frozen-input sample; timing is not a guarantee and maps are not bitwise identical"}
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output / "reference.npz", **reference)
    np.savez_compressed(output / "compiled.npz", **native)
    (output / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rays", type=int, default=8192)
    args = parser.parse_args()
    benchmark(args.request, args.output, args.rays)
