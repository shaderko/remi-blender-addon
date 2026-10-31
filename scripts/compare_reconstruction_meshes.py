"""Render arbitrary research meshes from the same held-out cameras, including clay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark_splat_mesh import make_report, run, SCRIPTS
from prepare_stereo import sha256


def compare(views, methods, blender, output):
    views, output, blender = [Path(p).resolve() for p in (views, output, blender)]
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    dataset = json.loads((views / "dataset.json").read_text())
    if sha256(dataset["source_archive"]) != dataset["source_sha256"]:
        raise ValueError("Source archive changed since rendering")
    if not blender.is_file():
        raise FileNotFoundError(blender)
    if not methods:
        raise ValueError("Provide at least one mesh")
    paths = {}
    for name, path in methods.items():
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name):
            raise ValueError(f"Unsafe method name: {name}")
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        paths[name] = str(path)
    output.mkdir(parents=True, exist_ok=True)
    (output / "methods.json").write_text(json.dumps(paths, indent=2), encoding="utf-8")
    for mode, flags in (("renders", []), ("clay", ["--clay"])):
        run([blender, "--background", "--factory-startup", "--python-exit-code", 1,
             "--python", SCRIPTS / "render_mesh_comparison.py", "--", "--views", views,
             "--methods", output / "methods.json", "--output", output / mode, *flags], output / f"{mode}.log")
    report = make_report(views, paths, output / "renders", output)
    (output / "geometry").mkdir()
    make_report(views, paths, output / "clay", output / "geometry", geometry_only=True)
    for name, record in report["methods"].items():
        print(name, json.dumps({"mean": record["mean"], "topology": record["topology"]}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--mesh", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    methods = {}
    for item in args.mesh:
        name, separator, path = item.partition("=")
        if not separator or name in methods:
            parser.error("Each --mesh must be a unique NAME=PATH")
        methods[name] = path
    compare(args.views, methods, args.blender, args.output)
