"""Blender 5.3+: render parallel stereo RGB/alpha pairs and held-out cameras.

blender --background --factory-startup --python-exit-code 1 --python
scripts/render_stereo_splats.py -- --archive splats.npz --output stereo_views

No depth pass is rendered: inferred surface depth must come from RGB stereo.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from splat_reconstruction.geometry import archive_bounds, make_rig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--views", type=int, default=48)
    parser.add_argument("--evaluation-views", type=int, default=12)
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--baseline-fraction", type=float, default=0.07,
                        help="Stereo baseline / bounds diameter")
    parser.add_argument("--distance", type=float, default=3.4)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--training-sides", choices=("both", "left"), default="both",
                        help="Use left only for posed multiview inference; stereo requires both")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    import bpy
    from mathutils import Matrix
    import numpy as np

    spec = importlib.util.spec_from_file_location(
        "remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
    from remi.blender.gaussian_splats import import_splats, native_splats_available
    from remi.integrations.triposplat.splat_data import load

    if not native_splats_available():
        raise RuntimeError("Native Gaussian rendering requires Blender 5.3+; ellipsoid fallback is not valid")
    if args.samples < 1:
        raise ValueError("Samples must be positive")
    archive, output = args.archive.resolve(), args.output.resolve()
    arrays = load(archive)
    rig = make_rig(archive_bounds(arrays), args.views, args.evaluation_views,
                   args.resolution, args.distance, args.baseline_fraction)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output is not empty; refusing to overwrite: {output}")
    output.mkdir(parents=True, exist_ok=True)
    rig.update({"source_archive": str(archive),
                "source_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "blender": bpy.app.version_string, "engine": "BLENDER_EEVEE",
                 "samples": args.samples, "training_sides": args.training_sides,
                 "depth_source": "RGB only; no rendered depth"})
    (output / "dataset.json").write_text(json.dumps(rig, indent=2), encoding="utf-8")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    import_splats(bpy.context, archive, display="NATIVE")
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = args.samples
    scene.render.resolution_x = scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.use_compositing = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.use_file_extension = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    data = bpy.data.cameras.new("Stereo camera")
    data.lens, data.sensor_width = rig["lens_mm"], rig["sensor_width_mm"]
    data.sensor_fit = "HORIZONTAL"
    data.clip_start, data.clip_end = rig["near"], rig["far"]
    camera = bpy.data.objects.new("Stereo camera", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    flip = np.diag([1.0, -1.0, -1.0, 1.0])

    def render(relative, c2w):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        camera.matrix_world = Matrix((np.asarray(c2w) @ flip).tolist())
        bpy.context.view_layer.update()
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)

    for pair in rig["pairs"]:
        for side in (("left", "right") if args.training_sides == "both" else ("left",)):
            render(pair[side], pair[f"{side}_camera_to_world_opencv"])
        print(f"Stereo pair {pair['index'] + 1}/{len(rig['pairs'])}", flush=True)
    for view in rig["evaluation_views"]:
        render(view["rgb"], view["camera_to_world_opencv"])
    (output / "render_complete.json").write_text(json.dumps({"complete": True}), encoding="utf-8")
    print(f"Stereo dataset: {output}", flush=True)


if __name__ == "__main__":
    main()
