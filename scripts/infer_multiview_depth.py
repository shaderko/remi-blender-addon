"""Infer posed DA3-Base depths from frozen native Gaussian RGBs, not splat Z."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np
from PIL import Image

from prepare_multiview import ROOT, verify_bundle
from prepare_stereo import sha256
from splat_reconstruction.multiview import nearest_context, resize_input, training_cameras, valid_prediction


def infer(views, output, bundle, resolution=756, context_views=8, device="cuda"):
    views, output, bundle = [Path(p).resolve() for p in (views, output, bundle)]
    dataset = json.loads((views / "dataset.json").read_text(encoding="utf-8"))
    if not (views / "render_complete.json").is_file():
        raise ValueError("Incomplete native splat renders")
    if sha256(dataset["source_archive"]) != dataset["source_sha256"]:
        raise ValueError("Source archive changed since rendering")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    manifest = verify_bundle(bundle)
    cameras = training_cameras(dataset)
    context_views = min(context_views, len(cameras))
    nearest_context(cameras, 0, context_views)
    images, alphas, intrinsics = [], [], []
    for view in cameras:
        with Image.open(views / view["rgb"]) as image:
            if image.size != tuple(dataset["resolution"]):
                raise ValueError("Training image/calibration size mismatch")
            rgb, alpha, k = resize_input(np.asarray(image.convert("RGBA")), dataset["intrinsics"], resolution)
        images.append(rgb)
        alphas.append(alpha)
        intrinsics.append(k)
    sys.path.insert(0, str(bundle / "source/src"))
    import torch
    from depth_anything_3.api import DepthAnything3

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    torch.set_num_threads(8)
    started = time.perf_counter()
    model = DepthAnything3.from_pretrained(str(bundle / "model")).to(device).eval()
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, view in enumerate(cameras):
        selected = nearest_context(cameras, index, context_views)
        exts = np.asarray([np.linalg.inv(cameras[i]["camera"]) for i in selected], dtype=np.float32)
        ks = np.asarray([intrinsics[i] for i in selected], dtype=np.float32)
        before = time.perf_counter()
        prediction = model.inference(
            image=[images[i] for i in selected], extrinsics=exts, intrinsics=ks,
            align_to_input_ext_scale=True, process_res=resolution,
            process_res_method="upper_bound_resize", infer_gs=False,
        )
        if (not np.allclose(prediction.extrinsics, exts[:, :3], atol=1e-5)
                or not np.allclose(prediction.intrinsics, ks, atol=1e-4)):
            raise RuntimeError("DA3 did not preserve the supplied camera calibration")
        if prediction.conf is None or prediction.depth.shape[1:] != alphas[index].shape:
            raise RuntimeError("Unexpected DA3 depth/confidence shape")
        depth, confidence = valid_prediction(prediction.depth[0], prediction.conf[0], alphas[index],
                                             dataset["near"], dataset["far"])
        name = f"view_{view['index']:04d}.npz"
        np.savez_compressed(output / name, depth=depth, confidence=confidence,
                            raw_depth=prediction.depth[0], raw_confidence=prediction.conf[0],
                            rgb=images[index], alpha=alphas[index], intrinsics=intrinsics[index],
                            camera=view["camera"])
        foreground = alphas[index] >= 0.2
        records.append({"index": view["index"], "file": name, "rgb": view["rgb"],
                        "context_indices": [cameras[i]["index"] for i in selected],
                        "valid_pixels": int((depth > 0).sum()), "foreground_pixels": int(foreground.sum()),
                        "seconds": time.perf_counter() - before})
        print(f"DA3 {index + 1}/{len(cameras)}: {records[-1]['valid_pixels']:,} pixels", flush=True)
        del prediction
    report = {"schema": 1, "method": "DA3-Base pose-conditioned overlapping multiview contexts",
              "dataset": str(views), "dataset_sha256": sha256(views / "dataset.json"),
              "source_sha256": dataset["source_sha256"], "source_commit": manifest["source_commit"],
              "model_commit": manifest["model_commit"],
              "checkpoint_sha256": manifest["files"]["model/model.safetensors"]["sha256"],
              "resolution": resolution, "context_views": context_views, "alpha_threshold": 0.2,
              "confidence_mapping": "raw_score/(1+raw_score) * alpha; not calibrated probability",
              "held_out_views_used": 0, "depth_units": "camera-Z in source archive world units",
              "runtime": {"python": platform.python_version(), "platform": platform.platform(),
                          "packages": {name: importlib.metadata.version(name) for name in
                                       ("torch", "torchvision", "numpy", "scipy", "safetensors", "huggingface_hub", "omegaconf", "evo")}},
              "seconds": time.perf_counter() - started,
              "peak_pytorch_vram_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else None,
              "frames": records}
    (output / "depths.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "frames"}, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, default=ROOT / "models/multiview/da3-base")
    parser.add_argument("--resolution", type=int, default=756)
    parser.add_argument("--context-views", type=int, default=8)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args()
    infer(args.views, args.output, args.bundle, args.resolution, args.context_views, args.device)
