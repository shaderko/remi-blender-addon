"""Research-only surface-aware reoptimization of frozen Gaussian RGB renders.

Runs an external, pinned Spirula Windows tool. No DA3 or native depth is used.
Outputs and optimizer state stay separate from the original Gaussian model.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

import numpy as np

from benchmark_splat_mesh import run
from export_splat_colmap import export
from prepare_stereo import sha256
from prepare_surface_optimizer import ROOT, prepare
from splat_reconstruction.metrics import topology_metrics


def optimizer_environment(base=None):
    """Disable only Overwolf's optional Vulkan layers in this child process.

    These names/values are declared by Overwolf's installed layer manifests.
    No registry, parent environment, running app, or driver setting is changed.
    """
    env = dict(os.environ if base is None else base)
    env.update(DISABLE_VULKAN_OW_OVERLAY_LAYER="1", DISABLE_VULKAN_OW_OBS_CAPTURE="1")
    return env


def train_arguments(executable, data, initial, prefix, name, iterations, cap):
    """Explicit synthetic-camera, surface-regularized settings, without learned priors."""
    settings = {
        "data": str(data), "data-format": "colmap", "init-ply": str(initial),
        "output-dir-prefix": str(prefix), "output-dir-name": name,
        "num-iterations": iterations, "cap-max": cap, "train-resolution-divisor": 1,
        "disable-viewer": 1, "keep-viewer-alive": 0, "cache-images": "cpu",
        "scene-center": "none", "orientation-method": "none", "center-method": "none",
        "auto-scale-poses": 0, "relative-scale": 1, "train-frame": "points",
        "use-bilateral-grid": 0, "use-bilateral-grid-for-geometry": 0, "use-ppisp": 0,
        "load-depths": 0, "load-normals": 0, "normal-supervision-weight": 0,
        "median-normal-supervision-weight": 0, "background-mode": "color",
        "apply-loss-for-mask": 1, "normal-reg-warmup": 1000,
        "distortion-reg-warmup": 1000, "median-warmup": 1000,
        "refine-stop-num-iter": min(6000, iterations - 1000),
        "refine-stop-iter": max(1000, iterations - 1000),
        "steps-per-save": 2000, "save-only-latest-checkpoint": 0,
    }
    return [str(executable), "train", "meshing"] + [str(x) for key, value in settings.items() for x in ("--" + key, value)]


def parse_camera_probe(text):
    """Read only numeric calibration fields from the upstream debug dump.

    The pinned Windows build fails to JSON-escape image filename backslashes.
    Do not rewrite upstream data or use a permissive/evaluating JSON parser.
    Extract the required numeric arrays/scalar, parsing each as strict JSON.
    """
    result = {}
    for name in ("viewmats", "intrins", "widths", "heights", "train_frame_scale"):
        match = re.search(r'"' + name + r'"\s*:\s*(\[[^\]]*\]|[-+0-9.eE]+)', text)
        if match is None:
            raise ValueError(f"Missing external camera calibration field: {name}")
        value = json.loads(match[1])
        if not np.isfinite(np.asarray(value, dtype=float)).all():
            raise ValueError(f"Invalid external camera calibration field: {name}")
        result[name] = value
    return result


def validate_camera_probe(probe, dataset):
    actual = np.asarray(probe["viewmats"]).reshape(-1, 4, 4)
    expected = np.asarray([np.linalg.inv(pair["left_camera_to_world_opencv"]) for pair in dataset["pairs"]])
    if actual.shape != expected.shape or not np.allclose(actual, expected, atol=1e-5):
        raise RuntimeError("External optimizer camera frame differs from archive coordinates; inspect camera_probe.json")
    if not np.isclose(probe["train_frame_scale"], 1.0):
        raise RuntimeError("External optimizer rescaled source coordinates")
    k = np.asarray(dataset["intrinsics"])
    intrinsics = np.asarray(probe["intrins"]).reshape(-1, 4)
    expected_k = np.broadcast_to([k[0, 0], k[1, 1], k[0, 2], k[1, 2]], (len(expected), 4))
    if (intrinsics.shape != expected_k.shape or not np.allclose(intrinsics, expected_k, atol=1e-3)
            or len(probe["widths"]) != len(expected) or len(probe["heights"]) != len(expected)
            or np.any(np.asarray(probe["widths"]) != dataset["resolution"][0])
            or np.any(np.asarray(probe["heights"]) != dataset["resolution"][1])):
        raise RuntimeError("External optimizer changed camera intrinsics or image resolution")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--initial-splats", type=Path, required=True, help="Original 3DGS PLY, NOT a triangle mesh")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tool", type=Path, default=ROOT / "models/surface/spirula-2026.10.16")
    parser.add_argument("--iterations", type=int, default=8000)
    parser.add_argument("--cap", type=int, default=1000000)
    parser.add_argument("--direct-extraction", action="store_true",
                        help="Ablation: use the same camera-aware mesher without surface reoptimization")
    parser.add_argument("--visible-only", action="store_true",
                        help="Explicit visibility-gated variant: cull geometry unseen by all training cameras")
    args = parser.parse_args()
    output, initial, views = args.output.resolve(), args.initial_splats.resolve(), args.views.resolve()
    if not initial.is_file():
        raise FileNotFoundError(initial)
    if args.iterations < 2000 or args.cap < 65536:
        raise ValueError("This pilot requires >=2000 iterations and >=65536 splat capacity")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    if os.name != "nt":
        raise RuntimeError("This pinned external binary is Windows-only")
    prepare(args.tool)
    executable = args.tool.resolve() / "tool/spirula.exe"
    output.mkdir(parents=True, exist_ok=True)
    dataset = json.loads((views / "dataset.json").read_text())
    export(views, output / "input")
    command = train_arguments(executable, output / "input", initial, output / "training", "fit", args.iterations, args.cap)
    tool_env = optimizer_environment()
    request = {"schema": 1, "tool_provenance": str(args.tool.resolve() / "provenance.json"),
               "source_sha256": dataset["source_sha256"], "initial_ply_sha256": sha256(initial),
               "views": str(views), "held_out_views_used": 0,
               "direct_extraction_ablation": args.direct_extraction,
               "visibility_culling_enabled": args.visible_only,
               "command": None if args.direct_extraction else command,
               "process_environment": {name: tool_env[name] for name in
                                       ("DISABLE_VULKAN_OW_OVERLAY_LAYER", "DISABLE_VULKAN_OW_OBS_CAPTURE")},
               "warning": "Research configuration, not a validated replacement for Create Mesh"}
    (output / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
    # Independently check the external parser's unnormalized camera frame before
    # spending time optimizing. No viewer or network listener is started.
    probe_command = train_arguments(executable, output / "input", initial, output / "probe", "calibration", args.iterations, args.cap)
    env = dict(tool_env, SS_DUMP_CAMERAS=str(output / "camera_probe.json"))
    with (output / "camera_probe.log").open("w", encoding="utf-8") as stream:
        result = subprocess.run(probe_command, env=env, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Camera preflight failed; see {output / 'camera_probe.log'}")
    probe = parse_camera_probe((output / "camera_probe.json").read_text())
    (output / "camera_probe_validated.json").write_text(json.dumps(probe, indent=2), encoding="utf-8")
    validate_camera_probe(probe, dataset)
    started = time.perf_counter()
    if not args.direct_extraction:
        run(command, output / "training.log", env=tool_env)
    training_seconds = 0.0 if args.direct_extraction else time.perf_counter() - started
    mesh_dir = output / "extraction"
    mesh_dir.mkdir()
    checkpoint = initial if args.direct_extraction else output / "training/fit"
    mesh_command = [str(executable), "mesh", str(checkpoint),
                    "--data", str(output / "input"), "--output", str(mesh_dir / "mesh"),
                    "--format", "ply", "--color", "vertex", "--merge-factor", "0",
                    "--floater-min-faces", "0", "--fill-hole-ratio", "0", "--fill-hole-max-edges", "0",
                    "--quality-iters", "0", "--degenerate-angle-deg", "0", "--cull-unseen", str(int(args.visible_only)),
                    "--bisection-iters", "6", "--max-cameras", "-1", "--carve-k", "3"]
    run(mesh_command, output / "extraction.log", env=tool_env)
    paths = list(mesh_dir.glob("*.ply"))
    if len(paths) != 1:
        raise RuntimeError("Expected exactly one extracted PLY")
    import trimesh
    mesh = trimesh.load(paths[0], process=False, force="mesh")
    extraction_log = (output / "extraction.log").read_text(encoding="utf-8", errors="replace")
    angles = re.search(r"smallest angle: ([0-9.eE+-]+) deg \(faces under 5 deg: (\d+), under 15 deg: (\d+)\)", extraction_log)
    report = {**request, "training_seconds": training_seconds, "mesh_command": mesh_command,
              "mesh": str(paths[0]), "mesh_sha256": sha256(paths[0]), "topology": topology_metrics(mesh),
              "postprocessing": "edge merging, component removal, hole filling, and mesh quality relaxation disabled; visibility culling explicitly recorded",
              "upstream_topology_handling": "upstream still splits non-manifold vertices; inspect extraction.log",
              "triangle_angle_diagnostics": {"minimum_degrees": float(angles[1]),
                                             "faces_below_5_degrees": int(angles[2]),
                                             "faces_below_15_degrees": int(angles[3])} if angles else None,
              "reference": "splat render agreement is not independent ground-truth geometry"}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
