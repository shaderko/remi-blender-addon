"""Compare shipped density, pre-cleanup density, and learned stereo on held-out views.

Run rendering and stereo inference first; see docs/stereo_mesh_benchmark.md.
Uses fresh output directories and never modifies source archives or the UI.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

from prepare_stereo import sha256
from splat_reconstruction.metrics import image_metrics, topology_metrics


SCRIPTS = Path(__file__).resolve().parent


def run(command, log):
    print(f"Running {log.stem}", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run([str(part) for part in command], stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
        raise RuntimeError(f"Stage failed; see {log}\n{tail}")


def make_report(views, methods, renders, output):
    import trimesh
    dataset = json.loads((views / "dataset.json").read_text())
    report = {"schema": 1, "source_sha256": dataset["source_sha256"],
              "reference": "held-out renders of source splat, NOT ground-truth geometry",
              "held_out_views": len(dataset["evaluation_views"]), "methods": {}}
    cells = []
    labels = ["Source splat"] + list(methods)
    size = 320
    sheet = Image.new("RGB", (size * len(labels), (size + 40) * len(dataset["evaluation_views"])), "#17191e")
    draw = ImageDraw.Draw(sheet)
    for method, mesh_path in methods.items():
        mesh = trimesh.load(mesh_path, process=False, force="mesh")
        report["methods"][method] = {"mesh": str(mesh_path), "topology": topology_metrics(mesh), "views": []}
    for row, view in enumerate(dataset["evaluation_views"]):
        reference_path = views / view["rgb"]
        with Image.open(reference_path) as image:
            reference = np.asarray(image.convert("RGBA"))
        paths = [reference_path] + [renders / method / f"view_{view['index']:04d}.png" for method in methods]
        images = []
        for col, (label, path) in enumerate(zip(labels, paths)):
            with Image.open(path) as image:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "#202228")
                background.alpha_composite(rgba)
                thumb = background.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
                sheet.paste(thumb, (col * size, row * (size + 40) + 40))
                draw.text((col * size + 8, row * (size + 40) + 12), f"View {view['index']}: {label}", fill="white")
                stream = io.BytesIO()
                background.convert("RGB").save(stream, format="PNG")
                images.append('<td><button type="button" title="Toggle full resolution" '
                              'onclick="this.classList.toggle(\'zoom\')">'
                              f'<img width="320" alt="{html.escape(label)} view {view["index"]}" '
                              f'src="data:image/png;base64,{base64.b64encode(stream.getvalue()).decode()}"></button></td>')
                if col:
                    report["methods"][label]["views"].append({"index": view["index"], **image_metrics(reference, np.asarray(rgba))})
        cells.append("<tr>" + "".join(images) + "</tr>")
    summary = []
    for method, record in report["methods"].items():
        record["mean"] = {key: float(np.mean([view[key] for view in record["views"] if view[key] is not None]))
                          if any(view[key] is not None for view in record["views"]) else None
                          for key in record["views"][0] if key != "index"}
        mean = record["mean"]
        summary.append(f"<li>{html.escape(method)}: silhouette IoU {mean['silhouette_iou']:.3f}; "
                       f"missing foreground {mean['missing_foreground_fraction']:.1%}; "
                       f"extra foreground {mean['extra_foreground_fraction']:.1%}</li>")
    (output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    sheet.save(output / "comparison.png")
    document = '<!doctype html><meta charset="utf-8"><title>Splat mesh benchmark</title>'
    document += '<style>body{background:#17191e;color:#eee;font:16px system-ui}td{padding:2px}th{text-align:left}'
    document += 'button{border:0;padding:0;background:none;cursor:zoom-in}.zoom{cursor:zoom-out}.zoom img{width:auto}</style>'
    document += '<h1>Held-out splat → mesh comparison</h1><p>Reference agreement, not ground-truth accuracy. '
    document += 'PSNR is restricted to common foreground and cannot compensate for missing geometry. '
    document += 'No blanket hole filling; legitimate open surfaces must remain open. Click an image for full resolution.</p><ul>' + "".join(summary) + '</ul>'
    document += '<table><tr>' + ''.join(f'<th>{html.escape(label)}</th>' for label in labels) + '</tr>' + ''.join(cells) + '</table>'
    (output / "comparison.html").write_text(document, encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--depths", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--density-resolution", type=int, default=224)
    parser.add_argument("--fusion-resolution", type=int, default=256)
    parser.add_argument("--min-views", type=int, default=2)
    args = parser.parse_args()
    views, depths, output = args.views.resolve(), args.depths.resolve(), args.output.resolve()
    dataset = json.loads((views / "dataset.json").read_text())
    archive = Path(dataset["source_archive"])
    if sha256(archive) != dataset["source_sha256"]:
        raise ValueError("Source archive changed since rendering")
    if not args.blender.is_file():
        raise ValueError("Blender executable not found")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    run([sys.executable, "-B", SCRIPTS / "mesh_density_baselines.py", "--archive", archive,
         "--output", output / "density", "--resolution", args.density_resolution], output / "density.log")
    run([sys.executable, "-B", SCRIPTS / "fuse_stereo_depth.py", "--views", views,
         "--depths", depths, "--output", output / "stereo", "--resolution", args.fusion_resolution,
         "--min-views", args.min_views], output / "fusion.log")
    methods = {"density_pre_cleanup": str(output / "density/density_pre_cleanup.ply"),
               "density_shipped": str(output / "density/density_shipped.ply"),
               "learned_stereo": str(output / "stereo/mesh.ply")}
    (output / "methods.json").write_text(json.dumps(methods, indent=2), encoding="utf-8")
    run([args.blender, "--background", "--factory-startup", "--python-exit-code", 1,
         "--python", SCRIPTS / "render_mesh_comparison.py", "--", "--views", views,
         "--methods", output / "methods.json", "--output", output / "renders"], output / "comparison_render.log")
    report = make_report(views, methods, output / "renders", output)
    (output / "run.json").write_text(json.dumps({"seconds": time.perf_counter() - started,
                                                "arguments": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}}, indent=2), encoding="utf-8")
    for method, record in report["methods"].items():
        print(method, json.dumps(record["mean"]), flush=True)
    print(f"Comparison: {output / 'comparison.html'}", flush=True)


if __name__ == "__main__":
    main()
