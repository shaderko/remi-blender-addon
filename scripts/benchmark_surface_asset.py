"""Run source rebuild on real, read-only asset arrays from diagnose_surface_asset.

Blender --background --factory-startup --python-exit-code 1 --python this.py --
--cache <diagnosis folder> --output <folder> --mesh 0 1 --error .005 .01
The baseline is the ORIGINAL triangular surface, not a native solver result.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
from remi.features.retopology.instant_meshes.adaptive import BVHSurface
from remi.features.retopology.instant_meshes.surface_adaptive import SurfacePatchRemesher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mesh", type=int, nargs="+", required=True)
    parser.add_argument("--error", type=float, nargs="+", default=[.005])
    parser.add_argument("--max-seconds", type=float, default=30)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if os.environ.get("REMI_TEST_DEPENDENCIES"):
        sys.path.insert(0, os.environ["REMI_TEST_DEPENDENCIES"])
    metadata = json.loads((args.cache / "report.json").read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"source_sha256": metadata["sha256"], "baseline": "source triangles", "cases": []}
    for index in args.mesh:
        archive = np.load(args.cache / f"source-{index}.npz")
        vertices, faces = archive["vertices"], archive["faces"]
        tree = BVHTree.FromPolygons([Vector(v) for v in vertices], faces.tolist(), all_triangles=True)
        for fraction in args.error:
            task = SurfacePatchRemesher(vertices, faces, BVHSurface(tree),
                fallback=(vertices, np.column_stack((faces, faces[:, 2]))),
                error=float(np.linalg.norm(np.ptp(vertices, axis=0)))*fraction, max_seconds=args.max_seconds)
            for wait in task.run():
                if isinstance(wait, float) and wait > 0:
                    time.sleep(wait)
            result = dict(task.report, mesh_index=index, error_fraction=fraction)
            report["cases"].append(result)
            print("REMI_REAL_SURFACE_BENCHMARK", index, fraction, "rebuilt", result["rebuilt"],
                  "faces", result["output_faces"], "patches", result["patches"],
                  "phase", result.get("failed_phase", "complete"), result["reason"], flush=True)
            if result["rebuilt"]:
                np.savez_compressed(args.output / f"mesh-{index}-{fraction}.npz", vertices=task.result[0], faces=task.result[1])
            (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print("REMI_REAL_SURFACE_BENCHMARK_OK", args.output / "report.json", flush=True)


if __name__ == "__main__":
    main()
