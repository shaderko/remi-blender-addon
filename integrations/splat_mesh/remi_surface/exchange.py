"""Lossless NPZ snapshots, standard 3DGS PLY encoding, and posed RGB export."""
from pathlib import Path
import zipfile

import numpy as np

from .common import write_json
from .geometry import training_cameras


def load_gaussians(path):
    with zipfile.ZipFile(path) as bundle:
        if sum(entry.file_size for entry in bundle.infolist()) > 32 * 1024 * 1024:
            raise ValueError("Gaussian archive exceeds the supported size")
    with np.load(path, allow_pickle=False) as bundle:
        if int(bundle["schema"]) != 1:
            raise ValueError("Unsupported Gaussian archive schema")
        arrays = {name: np.asarray(bundle[name], dtype=np.float32) for name in
                  ("positions", "scales", "rotations", "opacities", "sh_dc")}
    count = len(arrays["positions"])
    if not 0 < count <= 262144:
        raise ValueError("Expected 1–262144 source Gaussians")
    for name, width in (("positions", 3), ("scales", 3), ("rotations", 4), ("opacities", 0), ("sh_dc", 3)):
        expected = (count, width) if width else (count,)
        if arrays[name].shape != expected or not np.isfinite(arrays[name]).all():
            raise ValueError(f"Invalid Gaussian array: {name}")
    if np.any(arrays["scales"] <= 0) or np.any((arrays["opacities"] < 0) | (arrays["opacities"] > 1)):
        raise ValueError("Invalid Gaussian scales or opacity")
    if not np.allclose(np.linalg.norm(arrays["rotations"], axis=1), 1, atol=1e-3):
        raise ValueError("Expected unit WXYZ Gaussian rotations")
    return arrays


def write_gaussian_ply(path, arrays):
    """Encode activated scales/opacity as the standard log/logit 3DGS fields."""
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
             "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    count = len(arrays["positions"])
    opacity = np.clip(arrays["opacities"].astype(np.float64), 1e-7, 1 - 1e-7)
    values = np.column_stack((arrays["positions"], np.zeros((count, 3)), arrays["sh_dc"],
                              np.log(opacity / (1 - opacity)), np.log(arrays["scales"]), arrays["rotations"]))
    data = np.empty(count, dtype=[(name, "<f4") for name in names])
    for i, name in enumerate(names):
        data[name] = values[:, i]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex " + str(count) + "\n"
    header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
    with Path(path).open("xb") as stream:
        stream.write(header.encode("ascii"))
        data.tofile(stream)
    return Path(path)


def export_colmap(views, output, dataset, arrays, cancelled=lambda: None):
    from PIL import Image
    from scipy.spatial.transform import Rotation

    views, output = Path(views), Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("COLMAP output is not empty")
    cameras = training_cameras(dataset)
    images, masks, sparse = output / "images", output / "masks", output / "sparse/0"
    for path in (images, masks, sparse):
        path.mkdir(parents=True, exist_ok=True)
    width, height = dataset["resolution"]
    k = np.asarray(dataset["intrinsics"])
    (sparse / "cameras.txt").write_text(
        "# CAMERA_ID MODEL WIDTH HEIGHT PARAMS[]\n"
        f"1 PINHOLE {width} {height} {k[0, 0]:.12g} {k[1, 1]:.12g} {k[0, 2]:.12g} {k[1, 2]:.12g}\n", encoding="ascii")
    lines = []
    for i, camera in enumerate(cameras, 1):
        cancelled()
        name = f"pair_{camera['index']:04d}_left.png"
        source = (views / camera["rgb"]).resolve()
        if not source.is_relative_to(views.resolve()):
            raise ValueError("Training image path escapes the view directory")
        with Image.open(source) as image:
            if image.size != (width, height):
                raise ValueError("Training image/calibration size mismatch")
            rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255
        Image.fromarray(np.rint(rgba[..., :3] * rgba[..., 3:] * 255).astype(np.uint8)).save(images / name)
        Image.fromarray(np.rint(rgba[..., 3] * 255).astype(np.uint8)).save(masks / name)
        w2c = np.linalg.inv(camera["camera"])
        q = Rotation.from_matrix(w2c[:3, :3]).as_quat()[[3, 0, 1, 2]]
        parameters = " ".join(f"{value:.12g}" for value in np.r_[q, w2c[:3, 3]])
        lines.append(f"{i} {parameters} 1 {name}\n\n")
    (sparse / "images.txt").write_text("# Known cameras; no feature tracks\n" + "".join(lines), encoding="ascii")
    colors = np.rint(np.clip(arrays["sh_dc"] * 0.28209479177387814 + 0.5, 0, 1) * 255).astype(np.uint8)
    with (sparse / "points3D.txt").open("w", encoding="ascii") as stream:
        stream.write("# Gaussian-center initialization, NOT independently measured geometry\n")
        for i, (point, color) in enumerate(zip(arrays["positions"], colors), 1):
            stream.write(f"{i} {point[0]:.9g} {point[1]:.9g} {point[2]:.9g} "
                         f"{int(color[0])} {int(color[1])} {int(color[2])} 0\n")
    write_json(output / "export.json", {"schema": 1, "training_views": len(cameras),
                                       "source_sha256": dataset["source_sha256"], "held_out_views_used": 0})
    return output
