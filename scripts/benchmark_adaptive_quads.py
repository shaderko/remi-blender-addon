"""Full/half/quarter native budgets versus adaptive postprocessing, no inference.

Blender --background --factory-startup --python-exit-code 1 --python this.py --
--output <folder> [--target-faces 1200]. Writes measured reports and NPZ meshes.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT/"__init__.py", submodule_search_locations=[str(ROOT)])
module = importlib.util.module_from_spec(spec)
sys.modules["remi"] = module
spec.loader.exec_module(module)
from remi.features.retopology.instant_meshes._native import Session
from remi.features.retopology.instant_meshes.adaptive import QuadSimplifier, BVHSurface
from remi.features.retopology.instant_meshes.quality import mesh_quality

sys.path.insert(0, str(ROOT/"tests"))
from test_instant_meshes_adaptive import grid, HeightSurface
from instant_meshes_fixtures import sphere, cylinder


def bvh(mesh):
    return BVHTree.FromPolygons([Vector(v) for v in mesh[0]], mesh[1].tolist())


def errors(source, output):
    measurements = {}
    for name, first, second in (("source_to_output", source, output), ("output_to_source", output, source)):
        tree = bvh(second)
        points = np.vstack((first[0], first[0][first[1]].mean(axis=1)))
        ids = np.linspace(0, len(points)-1, min(len(points), 8192)).astype(int)
        distances = np.array([tree.find_nearest(Vector(points[i]))[3] for i in ids])
        measurements[name] = {"p95": float(np.quantile(distances, .95)), "max": float(distances.max())}
    return measurements


def solve(source, target):
    vertices, faces = source
    triangles = np.concatenate((faces[:, [0, 1, 2]], faces[:, [0, 2, 3]])) if faces.shape[1] == 4 else faces
    session = Session(vertices, triangles, target_faces=max(100, target), pure_quad=True,
                      crease_angle=35, align_boundaries=True, deterministic=True)
    try:
        for method in (session.start_orientation, session.start_position):
            method()
            deadline = time.monotonic()+120
            while session.active:
                assert time.monotonic() < deadline, "Native solve timed out"
                time.sleep(.005)
        vertices, faces, _normals = session.extract(strict=False)
        return np.array(vertices), np.array(faces)
    finally:
        session.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-faces", type=int, default=1200)
    parser.add_argument("--error", type=float, default=.002)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if args.target_faces < 400 or not 0 < args.error <= .02:
        parser.error("Use at least 400 faces and shape error in (0, .02]")
    args.output.mkdir(parents=True, exist_ok=True)
    cases = {"plane": lambda: grid(33), "localized_detail": lambda: grid(33, HeightSurface.height),
             "sphere": sphere, "cylinder": cylinder}
    report = {"blender": bpy.app.version_string, "target_faces": args.target_faces,
              "error_fraction": args.error, "cases": {}}
    for name, fixture in cases.items():
        source = fixture()
        reference = BVHSurface(bvh(source))
        full = solve(source, args.target_faces)
        result = {}
        limit = float(np.linalg.norm(np.ptp(source[0], axis=0)))*args.error
        for ratio in (1, .5, .25):
            native = full if ratio == 1 else solve(source, int(args.target_faces*ratio))
            task = QuadSimplifier(*full, reference, target_faces=int(len(full[1])*ratio), error=limit,
                                  max_seconds=120)
            started = time.monotonic()
            for _ in task.run():
                pass
            result[str(ratio)] = {"native": dict(mesh_quality(*native), surface_error=errors(source, native)),
                                  "adaptive": dict(task.report, surface_error=errors(source, task.result),
                                                   benchmark_seconds=time.monotonic()-started)}
            for method, mesh in (("native", native), ("adaptive", task.result)):
                np.savez_compressed(args.output/f"{name}-{ratio}-{method}.npz", vertices=mesh[0], faces=mesh[1])
            print("REMI_ADAPTIVE_BENCHMARK", name, ratio, len(native[1]), len(task.result[1]), task.report["reason"], flush=True)
        report["cases"][name] = result
        (args.output/"report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print("REMI_ADAPTIVE_BENCHMARK_OK", args.output/"report.json", flush=True)


if __name__ == "__main__":
    main()
