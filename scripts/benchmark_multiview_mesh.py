"""Compare dense DA3 raw/consistent meshes to an existing density baseline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from benchmark_splat_mesh import make_report, run, SCRIPTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--depths", type=Path, required=True)
    parser.add_argument("--density-mesh", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--surface-band-ablation", action="store_true",
                        help="Also diagnose destructive distant free-space carving")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    for path in (args.density_mesh, args.blender):
        if not path.is_file():
            raise FileNotFoundError(path)
    output.mkdir(parents=True, exist_ok=True)
    methods = {"density_shipped": str(args.density_mesh.resolve())}
    variants = [("da3_raw", ["--raw"]), ("da3_consistent", [])]
    if args.surface_band_ablation:
        variants.append(("da3_surface_band", ["--surface-band-only"]))
    for method, flags in variants:
        run([sys.executable, "-B", SCRIPTS / "fuse_multiview_depth.py",
             "--views", args.views.resolve(), "--depths", args.depths.resolve(),
             "--output", output / method, "--resolution", args.resolution, *flags], output / f"{method}.log")
        methods[method] = str(output / method / "mesh.ply")
    (output / "methods.json").write_text(json.dumps(methods, indent=2), encoding="utf-8")
    for mode, flags in (("renders", []), ("clay", ["--clay"])):
        run([args.blender, "--background", "--factory-startup", "--python-exit-code", 1,
             "--python", SCRIPTS / "render_mesh_comparison.py", "--", "--views", args.views.resolve(),
             "--methods", output / "methods.json", "--output", output / mode, *flags], output / f"{mode}.log")
    report = make_report(args.views.resolve(), methods, output / "renders", output)
    # Clay images are for visual inspection only, not RGB similarity scoring.
    clay = output / "geometry"
    clay.mkdir()
    make_report(args.views.resolve(), methods, output / "clay", clay, geometry_only=True)
    for method, record in report["methods"].items():
        print(method, json.dumps({"mean": record["mean"], "topology": record["topology"]}), flush=True)
    print(f"Comparison: {output / 'comparison.html'}", flush=True)


if __name__ == "__main__":
    main()
