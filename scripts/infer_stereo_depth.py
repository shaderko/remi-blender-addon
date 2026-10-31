"""Infer metric surface depths from rendered RGB pairs (never from splat Z)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from prepare_stereo import sha256
from splat_reconstruction.dlnr import DLNRStereo
from splat_reconstruction.stereo import disparity_to_depth


def load_rgba(path):
    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255
    alpha = rgba[..., 3]
    # Explicitly composite straight-alpha PNG on black for stereo inference.
    rgb = np.rint(rgba[..., :3] * alpha[..., None] * 255).astype(np.uint8)
    return rgb, alpha


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=root / "models" / "stereo" / "dlnr")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--iterations", type=int, default=16)
    parser.add_argument("--alpha-threshold", type=float, default=0.5)
    parser.add_argument("--lr-tolerance", type=float, default=1.0)
    args = parser.parse_args()
    dataset = json.loads((args.views / "dataset.json").read_text())
    if dataset.get("training_sides", "both") != "both":
        raise ValueError("Stereo inference requires both left and right training renders")
    if not (args.views / "render_complete.json").is_file():
        raise ValueError("Stereo render is incomplete")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"Output is not empty: {args.output}")
    # Verify the local snapshot before executing its modules.
    manifest = json.loads((args.model / "provenance.json").read_text())
    for name, record in manifest["files"].items():
        if sha256(args.model / name) != record["sha256"]:
            raise ValueError(f"Modified model bundle: {name}")
    checkpoint = args.model / "DLNR_Middlebury.pth"
    started = time.perf_counter()
    model = DLNRStereo(args.model / "source", checkpoint, args.device, args.iterations)
    import torch
    if args.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    width, height = dataset["resolution"]
    for pair in dataset["pairs"]:
        left, alpha_left = load_rgba(args.views / pair["left"])
        right, alpha_right = load_rgba(args.views / pair["right"])
        if left.shape != (height, width, 3) or right.shape != left.shape:
            raise ValueError("Stereo images do not match the recorded intrinsics")
        pair_started = time.perf_counter()
        disparity_left, disparity_right = model.predict(left, right)
        depth, confidence = disparity_to_depth(
            disparity_left, disparity_right, alpha_left, alpha_right,
            focal=dataset["intrinsics"][0][0], baseline=pair["baseline"],
            near=dataset["near"], far=dataset["far"],
            alpha_threshold=args.alpha_threshold, absolute_tolerance=args.lr_tolerance)
        relative = f"pair_{pair['index']:04d}.npz"
        np.savez_compressed(args.output / relative, depth=depth, confidence=confidence,
                            disparity_left=disparity_left, disparity_right=disparity_right)
        foreground = alpha_left >= args.alpha_threshold
        reports.append({"index": pair["index"], "file": relative,
                        "valid_pixels": int((depth > 0).sum()),
                        "foreground_pixels": int(foreground.sum()),
                        "foreground_retention": float((depth > 0).sum() / max(foreground.sum(), 1)),
                        "seconds": time.perf_counter() - pair_started})
        Image.fromarray(np.rint(confidence * 255).astype(np.uint8)).save(
            args.output / f"confidence_{pair['index']:04d}.png")
        print(f"Depth {pair['index'] + 1}/{len(dataset['pairs'])}: "
              f"{reports[-1]['foreground_retention']:.1%} foreground retained", flush=True)
    report = {"schema": 1, "method": "DLNR Middlebury RGB stereo + left/right consistency",
              "dataset": str(args.views.resolve()), "checkpoint_sha256": sha256(checkpoint),
              "source_commit": manifest["commit"], "iterations": args.iterations,
              "alpha_threshold": args.alpha_threshold, "lr_tolerance": args.lr_tolerance,
              "device": args.device, "torch": torch.__version__,
              "seconds": time.perf_counter() - started,
              "peak_vram_bytes": torch.cuda.max_memory_allocated() if args.device == "cuda" else None,
              "pairs": reports}
    (args.output / "depths.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not any(pair["valid_pixels"] for pair in reports):
        raise RuntimeError("Stereo produced no valid depths; inspect confidence maps")


if __name__ == "__main__":
    main()
