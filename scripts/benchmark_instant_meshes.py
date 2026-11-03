"""Compare unguided/guided topology at an identical requested face budget.

Run with Blender --background --factory-startup --python-exit-code 1
--python scripts/benchmark_instant_meshes.py -- --output <directory>.
No add-on installation, user scene, downloads or optional Python packages needed.
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
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py",
                                                submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = module
    spec.loader.exec_module(module)
from remi.features.retopology.instant_meshes._native import Session
from remi.features.retopology.instant_meshes.detection import detect_guides
from remi.features.retopology.instant_meshes.guides import project_detected_guides, send_guides
from remi.features.retopology.instant_meshes.quality import guide_quality, mesh_quality, prefer_guided

sys.path.insert(0, str(ROOT / "tests"))
from instant_meshes_fixtures import cylinder, washer, torus, sphere, plane


def bvh(vertices, faces):
    polygons = [face[:3] if len(face) == 4 and face[2] == face[3] else face for face in faces.tolist()]
    return BVHTree.FromPolygons([Vector(point) for point in vertices], polygons)


def wait(session, timeout=120):
    deadline = time.monotonic() + timeout
    while session.active:
        if time.monotonic() > deadline:
            session.stop()
            raise RuntimeError("Instant Meshes solve timed out")
        time.sleep(0.005)


def surface_error(source, output):
    source_bvh, output_bvh = bvh(*source), bvh(*output)
    distances = []
    for vertices, target in ((source[0], output_bvh), (output[0], source_bvh)):
        indices = np.linspace(0, len(vertices) - 1, min(len(vertices), 4096)).astype(int)
        distances.extend(target.find_nearest(Vector(vertices[index]))[3] for index in indices)
    diagonal = np.linalg.norm(np.ptp(source[0], axis=0))
    return float(np.quantile(distances, 0.95) / diagonal)


def compare(name, create, arguments):
    vertices, faces = create()
    result, outputs, reference_guides = {}, {}, None
    for guided in (False, True):
        started = time.monotonic()
        session = Session(vertices, faces, target_faces=arguments.target_faces, pure_quad=True,
                          crease_angle=35.0, align_boundaries=True, deterministic=True)
        try:
            surface = session.surface_snapshot()
            detected = detect_guides(vertices, faces, curvature=not arguments.no_curvature,
                                     target_scale=session.scale)
            projected = project_detected_guides(detected, bvh(surface[0], surface[1]),
                                                session.average_edge_length * 0.5,
                                                session.average_edge_length * 0.35)
            if reference_guides is None:
                reference_guides = projected
            if guided:
                send_guides(session, projected, strength=arguments.strength)
            session.start_orientation()
            wait(session)
            session.start_position()
            wait(session)
            out_vertices, out_faces, _ = session.extract(strict=False)
            outputs[guided] = out_vertices, out_faces
            metrics = mesh_quality(out_vertices, out_faces)
            metrics["surface_error_p95_in_diagonal"] = surface_error((vertices, faces), outputs[guided])
            metrics["guides"] = guide_quality(out_vertices, out_faces, reference_guides)
            metrics["warnings"] = list(session.output_warnings)
            metrics["seconds"] = time.monotonic() - started
            metrics["applied_guides"] = len(projected) if guided else 0
            metrics["detection_notes"] = detected.notes
            result["guided" if guided else "baseline"] = metrics
            np.savez_compressed(arguments.output / f"{name}-{'guided' if guided else 'baseline'}.npz",
                                vertices=out_vertices, faces=out_faces)
        finally:
            session.stop()
            del session
    selected, decision = prefer_guided(result["baseline"], result["guided"],
                                        result["baseline"]["guides"], result["guided"]["guides"])
    print("REMI_GUIDANCE_BENCHMARK", name, json.dumps({key: {
        "faces": value["faces"], "poles": value["interior_poles"],
        "aspect_p95": value["aspect_p95"], "angle_p95": value["angle_error_p95_deg"],
        "closed_loops": sum(item["closed_loop"] is True for item in value["guides"]),
        "warnings": value["warnings"]} for key, value in result.items()}), flush=True)
    result["selected"] = "guided" if selected else "baseline"
    result["decision"] = decision
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-faces", type=int, default=1500)
    parser.add_argument("--strength", type=float, default=0.65)
    parser.add_argument("--no-curvature", action="store_true")
    parser.add_argument("--cases", nargs="+", choices=("cylinder", "washer", "rounded_hole", "sphere", "plane"),
                        default=["cylinder", "washer", "rounded_hole", "sphere", "plane"])
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    if args.target_faces < 100 or not 0 < args.strength < 1:
        parser.error("Choose at least 100 faces and a strength in (0, 1)")
    args.output.mkdir(parents=True, exist_ok=True)
    cases = {"cylinder": cylinder, "washer": washer, "rounded_hole": torus, "sphere": sphere, "plane": plane}
    report = {"blender": bpy.app.version_string, "target_faces": args.target_faces,
              "strength": args.strength, "curvature": not args.no_curvature, "cases": {}}
    for name in args.cases:
        report["cases"][name] = compare(name, cases[name], args)
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print("REMI_GUIDANCE_BENCHMARK_OK", args.output / "report.json", flush=True)


if __name__ == "__main__":
    main()
