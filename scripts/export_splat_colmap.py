"""Export ONLY reconstruction cameras for surface-aware optimization experiments.

The sparse point cloud is initialized from Gaussian centers, not independent
SfM geometry or ground truth. Held-out evaluation images are never exported.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from prepare_stereo import sha256


ROOT = Path(__file__).resolve().parents[1]


def export(views, output, include_right=False):
    views, output = Path(views).resolve(), Path(output).resolve()
    dataset = json.loads((views / "dataset.json").read_text())
    if not (views / "render_complete.json").is_file():
        raise ValueError("Source render is incomplete")
    archive = Path(dataset["source_archive"])
    if sha256(archive) != dataset["source_sha256"]:
        raise ValueError("Source archive changed after rendering")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty: {output}")
    spec = importlib.util.spec_from_file_location("colmap_splat_data", ROOT / "integrations/triposplat/splat_data.py")
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    arrays = source.load(archive)
    output.mkdir(parents=True, exist_ok=True)
    images, masks, sparse = output / "images", output / "masks", output / "sparse" / "0"
    for path in (images, masks, sparse):
        path.mkdir(parents=True)
    width, height = dataset["resolution"]
    k = np.asarray(dataset["intrinsics"])
    (sparse / "cameras.txt").write_text(
        "# CAMERA_ID MODEL WIDTH HEIGHT PARAMS[]\n"
        f"1 PINHOLE {width} {height} {k[0, 0]:.12g} {k[1, 1]:.12g} {k[0, 2]:.12g} {k[1, 2]:.12g}\n",
        encoding="ascii")
    image_lines, records = [], []
    for pair in dataset["pairs"]:
        for side in (("left", "right") if include_right else ("left",)):
            name = f"pair_{pair['index']:04d}_{side}.png"
            with Image.open(views / pair[side]) as image:
                rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255
            if rgba.shape[:2] != (height, width):
                raise ValueError("Image size does not match calibration")
            Image.fromarray(np.rint(rgba[..., :3] * rgba[..., 3:] * 255).astype(np.uint8)).save(images / name)
            Image.fromarray(np.rint(rgba[..., 3] * 255).astype(np.uint8)).save(masks / name)
            w2c = np.linalg.inv(np.asarray(pair[f"{side}_camera_to_world_opencv"]))
            # COLMAP quaternion order is WXYZ; scipy returns XYZW.
            xyzw = Rotation.from_matrix(w2c[:3, :3]).as_quat()
            qwxyz = xyzw[[3, 0, 1, 2]]
            parameters = " ".join(f"{value:.12g}" for value in np.r_[qwxyz, w2c[:3, 3]])
            image_lines.append(f"{len(records) + 1} {parameters} 1 {name}\n\n")
            records.append({"name": name, "source_pair": pair["index"], "side": side})
    (sparse / "images.txt").write_text(
        "# IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME; next line has no feature tracks\n"
        + "".join(image_lines), encoding="ascii")
    colors = np.rint(np.clip(arrays["sh_dc"] * 0.28209479177387814 + 0.5, 0, 1) * 255).astype(np.uint8)
    with (sparse / "points3D.txt").open("w", encoding="ascii") as stream:
        stream.write("# Gaussian-center initialization; NOT triangulated SfM or ground truth\n")
        for index, (position, color) in enumerate(zip(arrays["positions"], colors), 1):
            stream.write(f"{index} {position[0]:.9g} {position[1]:.9g} {position[2]:.9g} "
                         f"{int(color[0])} {int(color[1])} {int(color[2])} 0\n")
    report = {"schema": 1, "source_dataset": str(views), "source_sha256": dataset["source_sha256"],
              "initialization": "Gaussian centers/colors, NOT independent SfM or ground truth; no feature tracks",
              "images": records, "withheld_evaluation_views": len(dataset["evaluation_views"]),
              "background": "RGB composited on black; original alpha masks supplied separately",
              "warning": "Adapting an optimizer to synthetic splat views remains an unvalidated research experiment"}
    (output / "export.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Exported {len(records)} calibrated training images to {output}", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-right", action="store_true")
    args = parser.parse_args()
    export(args.views, args.output, args.include_right)
