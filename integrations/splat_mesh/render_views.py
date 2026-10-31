"""Factory-startup Blender subprocess: render only the selected Gaussian snapshot."""
import argparse
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from remi_surface.common import check_cancel, sha256, status, write_json
from remi_surface.exchange import load_gaussians
from remi_surface.geometry import archive_bounds, make_rig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status-dir", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    import bpy
    from mathutils import Matrix
    import numpy as np

    root = HERE.parents[1]
    name = "_remi_surface_source"
    spec = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    adapter = __import__(name + ".blender.gaussian_splats", fromlist=["import_splats"])
    if not adapter.native_splats_available():
        raise RuntimeError("Create Mesh requires Blender 5.3+ with native Gaussian rendering; ellipsoid previews are not valid")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Render output is not empty")
    arrays = load_gaussians(args.archive)
    # Match the reviewed pilot's exact training cameras; no held-out RGBs are
    # rendered or consumed in production.
    rig = make_rig(archive_bounds(arrays), views=72, evaluation_views=12, resolution=1512)
    rig["evaluation_views"] = []
    rig.update(source_archive=str(args.archive.resolve()), source_sha256=sha256(args.archive),
               blender=bpy.app.version_string, engine="BLENDER_EEVEE", samples=32,
               training_sides="left", depth_source="RGB/alpha only; no source depth passes",
               camera_policy="72 training cameras from the reviewed 84-camera interleaved sphere")
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "dataset.json", rig)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    adapter.import_splats(bpy.context, args.archive, display="NATIVE")
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 32
    scene.render.resolution_x = scene.render.resolution_y = 1512
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
    data = bpy.data.cameras.new("Remi surface camera")
    data.lens, data.sensor_width, data.sensor_fit = rig["lens_mm"], rig["sensor_width_mm"], "HORIZONTAL"
    data.clip_start, data.clip_end = rig["near"], rig["far"]
    camera = bpy.data.objects.new("Remi surface camera", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    flip = np.diag([1.0, -1.0, -1.0, 1.0])
    for i, pair in enumerate(rig["pairs"], 1):
        check_cancel(args.status_dir)
        destination = args.output / pair["left"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        camera.matrix_world = Matrix((np.asarray(pair["left_camera_to_world_opencv"]) @ flip).tolist())
        bpy.context.view_layer.update()
        scene.render.filepath = str(destination)
        bpy.ops.render.render(write_still=True)
        status(args.status_dir, f"Rendering Gaussian views · {i}/72")
    write_json(args.output / "render_complete.json", {"schema": 1, "complete": True})


if __name__ == "__main__":
    main()
