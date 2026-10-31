"""Pinned surface-optimizer arguments and independent camera-frame validation."""
import json
import os
import re

import numpy as np


def optimizer_environment(base=None):
    env = dict(os.environ if base is None else base)
    env.update(DISABLE_VULKAN_OW_OVERLAY_LAYER="1", DISABLE_VULKAN_OW_OBS_CAPTURE="1")
    # A debug variable inherited from a developer shell must not skip training.
    env.pop("SS_DUMP_CAMERAS", None)
    return env


def train_arguments(executable, data, initial, prefix, name, iterations=8000, cap=1000000):
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
        "refine-stop-num-iter": min(6000, iterations - 1000), "refine-stop-iter": max(1000, iterations - 1000),
        "steps-per-save": 2000, "save-only-latest-checkpoint": 0,
    }
    return [str(executable), "train", "meshing"] + [str(x) for key, value in settings.items() for x in ("--" + key, value)]


def mesh_arguments(executable, checkpoint, data, destination):
    return [str(executable), "mesh", str(checkpoint), "--data", str(data), "--output", str(destination),
            "--format", "ply", "--color", "vertex", "--merge-factor", "0", "--floater-min-faces", "0",
            "--fill-hole-ratio", "0", "--fill-hole-max-edges", "0", "--quality-iters", "0",
            "--degenerate-angle-deg", "0", "--cull-unseen", "0", "--bisection-iters", "6",
            "--max-cameras", "-1", "--carve-k", "3"]


def parse_camera_probe(text):
    # The pinned Windows debug dump does not escape filename backslashes.
    # Read only numeric JSON fields; never evaluate or rewrite arbitrary text.
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
        raise RuntimeError("External surface tool changed the Gaussian camera frame")
    if not np.isclose(probe["train_frame_scale"], 1.0):
        raise RuntimeError("External surface tool changed the Gaussian world scale")
    k = np.asarray(dataset["intrinsics"])
    actual_k = np.asarray(probe["intrins"]).reshape(-1, 4)
    expected_k = np.broadcast_to([k[0, 0], k[1, 1], k[0, 2], k[1, 2]], (len(expected), 4))
    if (actual_k.shape != expected_k.shape or not np.allclose(actual_k, expected_k, atol=1e-3)
            or len(probe["widths"]) != len(expected) or len(probe["heights"]) != len(expected)
            or np.any(np.asarray(probe["widths"]) != dataset["resolution"][0])
            or np.any(np.asarray(probe["heights"]) != dataset["resolution"][1])):
        raise RuntimeError("External surface tool changed the camera intrinsics or image sizes")
