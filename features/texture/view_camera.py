"""Render the linked splat from the current viewport for view projection."""
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


def _render_opengl(context, area, region, path):
    with context.temp_override(area=area, region=region):
        bpy.ops.render.opengl(write_still=True, view_context=True)
    if not Path(path).is_file():
        raise RuntimeError("Viewport splat render did not produce an image")


def _save_render_state(scene):
    return {
        "engine": scene.render.engine,
        "filepath": scene.render.filepath,
        "format": scene.render.image_settings.file_format,
        "color_mode": scene.render.image_settings.color_mode,
        "color_depth": scene.render.image_settings.color_depth,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "film_transparent": scene.render.film_transparent,
        "view_transform": scene.view_settings.view_transform,
        "look": scene.view_settings.look,
        "exposure": scene.view_settings.exposure,
        "gamma": scene.view_settings.gamma,
        "hide": {obj.name: obj.hide_get() for obj in scene.objects},
    }


def _restore_render_state(scene, saved):
    scene.render.engine = saved["engine"]
    scene.render.filepath = saved["filepath"]
    scene.render.image_settings.file_format = saved["format"]
    scene.render.image_settings.color_mode = saved["color_mode"]
    scene.render.image_settings.color_depth = saved["color_depth"]
    scene.render.resolution_x = saved["resolution_x"]
    scene.render.resolution_y = saved["resolution_y"]
    scene.render.resolution_percentage = saved["resolution_percentage"]
    scene.render.film_transparent = saved["film_transparent"]
    scene.view_settings.view_transform = saved["view_transform"]
    scene.view_settings.look = saved["look"]
    scene.view_settings.exposure = saved["exposure"]
    scene.view_settings.gamma = saved["gamma"]
    for name, hidden in saved["hide"].items():
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.hide_set(hidden)


def _render_viewport_view(context, splat, output_black, output_white, area, region):
    """Render through the exact viewport and recover alpha from black/white."""
    scene = context.scene
    space = area.spaces.active
    output_black = Path(output_black)
    output_white = Path(output_white)
    output_black.parent.mkdir(parents=True, exist_ok=True)
    output_white.parent.mkdir(parents=True, exist_ok=True)
    saved = _save_render_state(scene)
    saved.update(
        {
            "shading_type": space.shading.type,
            "background_type": space.shading.background_type,
            "show_overlays": space.overlay.show_overlays,
            "world": scene.world,
        }
    )
    from .projection_area import state as projection_area

    theme = bpy.context.preferences.themes[0]
    gradient = theme.view_3d.space.gradients
    saved_theme = {
        "background_type": gradient.background_type,
        "high_gradient": tuple(gradient.high_gradient),
        "gradient": tuple(gradient.gradient),
    }
    was_suspended = projection_area.suspended
    try:
        projection_area.suspended = True
        space.shading.type = "MATERIAL"
        space.shading.background_type = "THEME"
        space.overlay.show_overlays = False
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"
        scene.render.image_settings.color_depth = "8"
        scene.render.resolution_x = int(region.width)
        scene.render.resolution_y = int(region.height)
        scene.render.resolution_percentage = 100
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        for obj in scene.objects:
            obj.hide_set(obj != splat)
        splat.hide_set(False)

        gradient.background_type = "SINGLE_COLOR"
        gradient.high_gradient = (0.0, 0.0, 0.0)
        scene.render.filepath = str(output_black)
        _render_opengl(context, area, region, output_black)

        gradient.high_gradient = (1.0, 1.0, 1.0)
        scene.render.filepath = str(output_white)
        _render_opengl(context, area, region, output_white)

        view = region.data.view_matrix.copy()
        combined = region.data.perspective_matrix.copy()
        projection = combined @ view.inverted()
        return {
            "image": str(output_black),
            "background": str(output_white),
            "view_matrix": [list(row) for row in view],
            "projection_matrix": [list(row) for row in projection],
            "width": int(region.width),
            "height": int(region.height),
        }
    finally:
        projection_area.suspended = was_suspended
        space.shading.type = saved["shading_type"]
        space.shading.background_type = saved["background_type"]
        space.overlay.show_overlays = saved["show_overlays"]
        scene.world = saved["world"]
        gradient.background_type = saved_theme["background_type"]
        gradient.high_gradient = saved_theme["high_gradient"]
        gradient.gradient = saved_theme["gradient"]
        _restore_render_state(scene, saved)


def render_splat_view(context, splat, output, output_white=None):
    """Render only the splat through the current viewport."""
    area, region = viewport_region(context)
    if area is None or region is None:
        raise RuntimeError("Project from View must be run from a 3D Viewport")
    if region.width < 16 or region.height < 16:
        raise RuntimeError("The 3D Viewport is too small to project from")
    if output_white is None:
        raise RuntimeError("Viewport splat rendering needs a background pass")
    return _render_viewport_view(context, splat, output, output_white, area, region)


def render_splat_normal_view(context, normal_splat, output_black, output_white):
    """Render the encoded-normal splat object through the same viewport pass."""
    area, region = viewport_region(context)
    if area is None or region is None:
        raise RuntimeError("Normal projection must be run from a 3D Viewport")
    return _render_viewport_view(context, normal_splat, output_black, output_white, area, region)
