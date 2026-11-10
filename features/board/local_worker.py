"""Isolated local asset worker. Run with Blender --background --factory-startup.

It never opens the user's project or uses the foreground active object. Flows
reuse Remi's validated automatic plan and session transactions in this process.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import time
import traceback
import uuid
import zipfile

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_dependencies(request):
    for path in request.get("dependency_paths", []):
        if isinstance(path, str) and Path(path).is_dir():
            sys.path.insert(0, path)
    if importlib.util.find_spec("pymeshlab") is not None:
        return
    # Installed extensions normally already provide this dependency. Source
    # sketch launches can unpack the checksum-pinned bundled wheel, never pip.
    lock = json.loads((ROOT / "wheels" / "lock.json").read_text(encoding="utf-8"))
    platform = "windows-x64" if sys.platform == "win32" else "macos-arm64" if sys.platform == "darwin" else "linux-x64"
    spec = lock[platform]
    wheel = ROOT / "wheels" / spec["filename"]
    if not wheel.is_file():
        return  # The feature's normal dependency preflight supplies the error.
    with wheel.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != spec["sha256"]:
            raise ValueError("Bundled MeshLab wheel checksum mismatch")
    # Keep paths short enough for older Windows Blender builds without long-path
    # support. The complete checksum is still verified before every extraction.
    cache = Path(bpy.utils.user_resource("DATAFILES")) / "remi" / "worker-packages" / spec["sha256"][:16]
    if not (cache / "ready.json").is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_name(cache.name + "-" + uuid.uuid4().hex[:8])
        temporary.mkdir()
        try:
            with zipfile.ZipFile(wheel) as archive:
                for item in archive.infolist():
                    parts = PurePosixPath(item.filename).parts
                    # Wheels may keep Python packages under .data/purelib or
                    # .data/platlib; flatten these like an extension install.
                    if len(parts) > 2 and parts[0].endswith(".data") and parts[1] in {"purelib", "platlib"}:
                        parts = parts[2:]
                    target = temporary.joinpath(*parts)
                    if not target.resolve().is_relative_to(temporary.resolve()):
                        raise ValueError("Invalid path in bundled wheel")
                    if item.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item) as source, target.open("wb") as destination:
                            shutil.copyfileobj(source, destination)
            (temporary / "ready.json").write_text(json.dumps({"sha256": spec["sha256"]}), encoding="utf-8")
            try:
                temporary.rename(cache)
            except FileExistsError:
                if not (cache / "ready.json").is_file():
                    raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    sys.path.insert(0, str(cache))


def load_addon():
    name = "remi_board_worker"
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def import_asset(path, addon):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    if path.suffix.lower() == ".ply":
        from importlib import import_module
        importer = import_module(addon.__name__ + ".blender.reconstructed_mesh")
        return importer.import_reconstructed_mesh(bpy.context, path)
    if path.suffix.lower() in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        if not meshes:
            raise ValueError("The board asset contains no mesh")
        obj = meshes[0]
        for other in bpy.context.selected_objects:
            other.select_set(False)
        for mesh in meshes:
            mesh.select_set(True)
        bpy.context.view_layer.objects.active = obj
        if len(meshes) > 1:
            # Manual Blender edits may split/add parts. Combine only inside
            # this disposable worker, never in the editable source scene.
            bpy.ops.object.join()
        return obj
    raise ValueError("Unsupported board mesh artifact")


def render_preview(obj, output, request):
    scene = bpy.context.scene
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    lower = Vector([min(point[axis] for point in points) for axis in range(3)])
    upper = Vector([max(point[axis] for point in points) for axis in range(3)])
    centre = (lower + upper) * 0.5
    radius = max((upper-lower).length * 0.5, 0.01)
    yaw, elevation = math.radians(request.get("yaw", 0)), math.radians(request.get("elevation", 0))
    outward = Vector((math.sin(yaw)*math.cos(elevation), -math.cos(yaw)*math.cos(elevation), math.sin(elevation)))
    camera = bpy.data.objects.new("Board preview camera", bpy.data.cameras.new("Board preview camera"))
    scene.collection.objects.link(camera)
    camera.location = centre + outward * (radius / math.sin(math.radians(20)) * 1.12)
    camera.rotation_euler = (centre-camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.lens = 36 / (2 * math.tan(math.radians(20)))
    scene.camera = camera
    for name, offset, energy, size in (("Key", (3,-4,5), 700, 4), ("Fill", (-4,-2,2), 350, 5)):
        light = bpy.data.objects.new(name, bpy.data.lights.new(name, "AREA"))
        scene.collection.objects.link(light)
        light.location = centre + Vector(offset) * max(radius, 0.5)
        light.rotation_euler = (centre-light.location).to_track_quat("-Z", "Y").to_euler()
        light.data.energy = energy * max(radius, 0.5)**2
        light.data.shape = "DISK"
        light.data.size = size * max(radius, 0.5)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.threads_mode = "FIXED"
    scene.render.threads = min(8, os.cpu_count() or 1)
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = str(output)
    bpy.ops.render.render(write_still=True)


def run(request, directory):
    operation = request.get("operation")
    if operation not in {"FLOW", "PREVIEW", "TEXTURE"}:
        raise ValueError("Unsupported board worker operation")
    source = Path(request["input"])
    if not source.is_file():
        raise ValueError("The source asset file is missing")
    if operation == "TEXTURE" and not Path(request.get("gaussian_source", "")).is_file():
        raise ValueError("The Gaussian texture source is missing")
    def status(phase, progress):
        if (directory / "cancel.request").exists():
            raise InterruptedError("Board job cancelled")
        write_json(directory / "status.json", {"phase": phase, "progress": progress})
    status("Loading asset", 0.02)
    addon = load_addon()
    registered = False
    try:
        if operation == "FLOW":
            prepare_dependencies(request)
            addon.register()
            registered = True
        obj = import_asset(source, addon)
        if operation == "TEXTURE":
            from importlib import import_module
            projection = import_module(addon.__name__ + ".blender.splat_projection")
            if not obj.data.uv_layers:
                status("Creating texture UVs", 0.18)
                uv = import_module(addon.__name__ + ".features.uv.engine").ensure_remi_uv(
                    obj, profile_id="NORMAL_BAKE", texture_size=2048, margin_px=4,
                    replace_existing=True)
                if not uv.success:
                    raise RuntimeError(uv.error or "Could not create texture UVs")
            status("Projecting Gaussian appearance", 0.25)
            temporary = directory / "texture_mesh.npz"
            projection.export_mesh_archive(bpy.context, obj, temporary)
            request_options = request["texture"]
            from importlib import import_module
            ProjectionJob = import_module(addon.__name__ + ".integrations.splat_project.client").ProjectionJob
            job = ProjectionJob.start(
                python=request["python"], archive=request["gaussian_source"],
                mesh=temporary, output=directory, texture_size=int(request_options["texture_size"]),
                samples=16, normal_smoothing=int(request_options["normal_smoothing"]),
                fill_holes=True, fill_background=True, margin_px=8, method="OCTREE",
                match_distance=float(request_options["match_distance"]),
                layer_width=float(request_options["layer_width"]),
                local_normal_mode="AUTO", smooth_rays=True, source="Remi board texture")
            try:
                while job.poll() is None:
                    state = job.status()
                    status(str(state.get("phase", "Projecting Gaussian appearance")), 0.3)
                    if (directory / "cancel.request").exists():
                        job.cancel()
                        raise InterruptedError("Board job cancelled")
                    time.sleep(0.1)
                paths = job.result()
            finally:
                if job.poll() is None:
                    job.cancel()
            projection.import_projection_result(obj, paths, source_label_value="Remi board texture",
                                                pack_images=True, normal_mode="PROJECTED")
            artifact = directory / "textured.glb"
            status("Saving textured GLB", 0.9)
            bpy.ops.export_scene.gltf(filepath=str(artifact), export_format="GLB",
                                      use_selection=True, export_image_format="AUTO")
        elif operation == "FLOW":
            from importlib import import_module
            flow_module = import_module(addon.__name__ + ".app.flow")
            automatic = import_module(addon.__name__ + ".workflow.automatic")
            application = import_module(addon.__name__ + ".app.application").get_application()
            document = request["flow"]
            flow_module.validate_plan(bpy.context, document)
            flow = automatic.AutomaticFlow(application.session, application.features)
            flow.start(bpy.context, document)
            while True:
                state = application.session.state(bpy.context)
                status(state.status, 0.05 + 0.8*flow.index/max(1, len(flow.actions)))
                outcome = flow.tick(bpy.context)
                if outcome == "FINISHED":
                    break
                if outcome in {"FAILED", "STOPPED"}:
                    raise RuntimeError(state.status)
            obj = bpy.context.active_object
            artifact = directory / "asset.glb"
            status("Saving processed asset", 0.9)
            bpy.ops.export_scene.gltf(filepath=str(artifact), export_format="GLB", use_selection=True)
        else:
            artifact = directory / "preview.png"
        status("Rendering preview", 0.94)
        preview = directory / "preview.png"
        preview_error = ""
        try:
            render_preview(obj, preview, request)
        except Exception as exc:
            if operation == "PREVIEW":
                raise
            # A thumbnail failure must never throw away a successful flow.
            preview_error = str(exc)
            traceback.print_exc()
        write_json(directory / "result.json", {"schema": 1, "artifact": str(artifact),
            "preview": str(preview) if preview.is_file() else "", "preview_error": preview_error})
        status("Complete", 1.0)
    finally:
        if registered:
            addon.unregister()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    directory = args.request.resolve().parent
    try:
        if args.request.stat().st_size > 256*1024:
            raise ValueError("Board request is too large")
        run(json.loads(args.request.read_text(encoding="utf-8")), directory)
    except Exception as exc:
        write_json(directory / "status.json", {"phase": "Failed", "error": str(exc)})
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
