"""Compare normal Instant Meshes, seed simplification and source patch rebuild.

Blender --background --factory-startup --python-exit-code 1 --python this.py --
--output <folder> [--target-faces 1200] [--error .005]. Counts are NOT matched.
"""

import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_adaptive_quads import bvh, errors, grid, HeightSurface, sphere, cylinder, solve
from remi.features.retopology.instant_meshes.adaptive import BVHSurface, QuadSimplifier
from remi.features.retopology.instant_meshes.surface_adaptive import SurfacePatchRemesher
from remi.features.retopology.instant_meshes.quality import mesh_quality


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-faces", type=int, default=1200)
    parser.add_argument("--error", type=float, default=.005)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if args.target_faces < 100 or not 0 < args.error <= .02:
        parser.error("Use at least 100 native faces and error in (0, .02]")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"blender": bpy.app.version_string, "error_fraction": args.error,
              "native_target_faces": args.target_faces, "equal_actual_counts": False, "cases": {}}
    for name, fixture in {"plane": lambda: grid(49),
                          "localized_detail": lambda: grid(49, HeightSurface.height),
                          "sphere": sphere, "cylinder": cylinder}.items():
        vertices, faces = source = fixture()
        triangles = np.stack((faces[:, [0, 1, 2]], faces[:, [0, 2, 3]]), axis=1).reshape(-1, 3) if faces.shape[1] == 4 else faces
        native = solve(source, args.target_faces)
        surface = BVHSurface(bvh(source))
        error = float(np.linalg.norm(np.ptp(vertices, axis=0)))*args.error
        tasks = {"seed_simplify": QuadSimplifier(*native, surface, target_faces=max(4, len(native[1])//4), error=error),
                 "surface_rebuild": SurfacePatchRemesher(vertices, triangles, surface, fallback=native, error=error)}
        measured = {"native": dict(mesh_quality(*native), surface_error=errors(source, native))}
        for method, task in tasks.items():
            for _ in task.run():
                pass
            centers = task.result[0][task.result[1]].mean(axis=1)
            measured[method] = dict(task.report, surface_error=errors(source, task.result))
            if name == "localized_detail":
                measured[method]["smooth_faces"] = int(np.sum(centers[:, 0] < -.4))
                measured[method]["detail_faces"] = int(np.sum(centers[:, 0] > .4))
            np.savez_compressed(args.output / f"{name}-{method}.npz", vertices=task.result[0], faces=task.result[1])
            print("REMI_SURFACE_BENCHMARK", name, method, len(native[1]), len(task.result[1]), task.report["reason"], flush=True)
        report["cases"][name] = measured
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print("REMI_SURFACE_BENCHMARK_OK", args.output / "report.json", flush=True)


if __name__ == "__main__":
    main()
