"""Render the linked splat from the current viewport camera for projection."""
from __future__ import annotations

from pathlib import Path

import bpy


def viewport_region(context):
    """Return the 3D viewport area and its WINDOW region, if one exists."""
    area = context.area
    if area is not None and area.type == "VIEW_3D":
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        if region is not None:
            return area, region
    screen = getattr(context, "screen", None)
    if screen is not None:
        for candidate in screen.areas:
            if candidate.type == "VIEW_3D":
                region = next((item for item in candidate.regions if item.type == "WINDOW"), None)
                if region is not None:
                    return candidate, region
    return None, None


def _temp_camera(context):
    region_data = context.region_data
    space_data = context.space_data
    if region_data is None or space_data is None or space_data.type != "VIEW_3D":
        raise RuntimeError("Project from View must be run from a 3D Viewport")

    if region_data.view_perspective == "CAMERA" and context.scene.camera is not None:
        return context.scene.camera, False

    camera_data = bpy.data.cameras.new("Remi View Camera")
    camera = bpy.data.objects.new("Remi View Camera", camera_data)
    context.scene.collection.objects.link(camera)
    camera.matrix_world = region_data.view_matrix.inverted()
    camera_data.lens = float(getattr(space_data, "lens", 50.0))
    camera_data.sensor_width = 36.0
    camera_data.sensor_fit = "AUTO"
    if region_data.view_perspective == "ORTHO":
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = float(getattr(region_data, "view_distance", 10.0))
    else:
        camera_data.type = "PERSP"
    return camera, True


def _resolution(context, base):
    region = context.region
    if region is None or region.width <= 0 or region.height <= 0:
        return int(base), int(base)
    aspect = region.width / max(region.height, 1)
    base = int(base)
    if aspect >= 1.0:
        return base, max(64, int(round(base / aspect)))
    return max(64, int(round(base * aspect))), base


def render_splat_view(context, splat, output, *, base_resolution=1024, mesh=None):
    """Render only the splat object from the current viewport camera.

    Returns the rendered image path plus the matching world-to-camera and
    projection matrices. The temporary camera and render settings are restored.
    """
    scene = context.scene
    camera, temporary_camera = _temp_camera(context)
    width, height = _resolution(context, base_resolution)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if mesh is None:
        mesh = context.view_layer.objects.active
    diagnostic = 1.0
    if mesh is not None:
        diagnostic = max(0.001, float(mesh.dimensions.length))
    distance = (camera.matrix_world.translation - (mesh.matrix_world.translation if mesh else camera.matrix_world.translation)).length
    camera.data.clip_start = max(1e-5, min(diagnostic * 1e-4, distance * 0.25))
    camera.data.clip_end = max(diagnostic * 100.0, distance * 10.0, 1.0)

    saved = {
        "camera": scene.camera,
        "engine": scene.render.engine,
        "x": scene.render.resolution_x,
        "y": scene.render.resolution_y,
        "percentage": scene.render.resolution_percentage,
        "transparent": scene.render.film_transparent,
        "filepath": scene.render.filepath,
        "format": scene.render.image_settings.file_format,
        "color_mode": scene.render.image_settings.color_mode,
        "samples": getattr(scene.eevee, "taa_render_samples", None),
        "clip_start": camera.data.clip_start,
        "clip_end": camera.data.clip_end,
        "view_transform": scene.view_settings.view_transform,
        "look": scene.view_settings.look,
        "exposure": scene.view_settings.exposure,
        "gamma": scene.view_settings.gamma,
        "object_hide_render": {obj.name: obj.hide_render for obj in scene.objects},
        "collection_hide_render": {collection.name: collection.hide_render for collection in bpy.data.collections},
    }
    result = None
    try:
        scene.camera = camera
        engines = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
        scene.render.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in engines else "BLENDER_EEVEE_NEXT"
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        scene.render.resolution_x = width
        scene.render.resolution_y = height
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.filepath = str(output)
        if saved["samples"] is not None:
            scene.eevee.taa_render_samples = 16
        for collection in bpy.data.collections:
            collection.hide_render = False
        for obj in scene.objects:
            obj.hide_render = obj != splat
        splat.hide_render = False
        bpy.ops.render.render(write_still=True)

        if not output.is_file():
            raise RuntimeError("The viewport splat render did not produce an image")
        depsgraph = context.evaluated_depsgraph_get()
        projection = camera.calc_matrix_camera(
            depsgraph, x=width, y=height, scale_x=1.0, scale_y=1.0
        )
        view = camera.matrix_world.inverted()
        result = {
            "image": str(output),
            "view_matrix": [list(row) for row in view],
            "projection_matrix": [list(row) for row in projection],
            "width": width,
            "height": height,
        }
    finally:
        scene.camera = saved["camera"]
        scene.render.engine = saved["engine"]
        scene.render.resolution_x = saved["x"]
        scene.render.resolution_y = saved["y"]
        scene.render.resolution_percentage = saved["percentage"]
        scene.render.film_transparent = saved["transparent"]
        scene.render.filepath = saved["filepath"]
        scene.render.image_settings.file_format = saved["format"]
        scene.render.image_settings.color_mode = saved["color_mode"]
        if saved["samples"] is not None and getattr(scene, "eevee", None) is not None:
            scene.eevee.taa_render_samples = saved["samples"]
        scene.view_settings.view_transform = saved["view_transform"]
        scene.view_settings.look = saved["look"]
        scene.view_settings.exposure = saved["exposure"]
        scene.view_settings.gamma = saved["gamma"]
        camera.data.clip_start = saved["clip_start"]
        camera.data.clip_end = saved["clip_end"]
        for name, value in saved["object_hide_render"].items():
            obj = bpy.data.objects.get(name)
            if obj is not None:
                obj.hide_render = value
        for name, value in saved["collection_hide_render"].items():
            collection = bpy.data.collections.get(name)
            if collection is not None:
                collection.hide_render = value
        if temporary_camera:
            camera_data = camera.data
            bpy.data.objects.remove(camera, do_unlink=True)
            if camera_data.users == 0:
                bpy.data.cameras.remove(camera_data)
    return result
