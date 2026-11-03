"""Combined image and 3D generation workspace."""

from pathlib import Path
import textwrap

import bpy
from bpy.types import Panel

from ...blender.gaussian_splats import splat_for_image
from ...blender.reconstructed_mesh import mesh_for_splat
from ...integrations.iris3b import MODEL_LABELS, missing_components
from ..generation.panel import draw_generation_settings, draw_mesh_controls
from .gallery import gallery
from .settings import dimensions

PAGE_SIZE = 6
COLUMNS = 3


def _note(layout, text, icon="INFO"):
    for index, line in enumerate(textwrap.wrap(text, width=48)):
        layout.label(text=line, icon=icon if index == 0 else "NONE")


def _selected_entry(entries, selected):
    if not selected:
        return None
    return next((entry for entry in entries if _same_path(selected, entry.path)), None)


def _same_path(first, second):
    try:
        return Path(bpy.path.abspath(str(first))).resolve() == Path(bpy.path.abspath(str(second))).resolve()
    except (OSError, TypeError, ValueError):
        return False


def _draw_card(layout, entry, selected, image_state, generation_state):
    box = layout.box()
    box.alert = selected
    icon = gallery.icon(entry.path)
    if icon:
        box.template_icon(icon_value=icon, scale=2.4)
    else:
        box.label(text="Preview", icon="IMAGE_DATA")
    busy_3d = (
        generation_state.busy
        and generation_state.source_image
        and _same_path(generation_state.source_image, entry.path)
    )
    if busy_3d:
        box.progress(
            factor=float(generation_state.progress),
            type="BAR",
            text=generation_state.status or "Generating 3D…",
        )
    else:
        box.label(text=f"{entry.model_label} · {entry.seed}", icon="IMAGE_DATA")
    splat = splat_for_image(entry.path)
    row = box.row(align=True)
    if busy_3d:
        row.label(text="3D", icon="TIME")
    elif splat is not None:
        activate = row.operator("remi.activate_splat", text="3D", icon="CHECKMARK")
        activate.image_path = str(entry.path)
    else:
        play = row.operator("remi.generate_splats", text="", icon="PLAY")
        play.image_path = str(entry.path)
    select = row.operator(
        "remi.select_image",
        text="",
        icon="RESTRICT_SELECT_OFF",
        depress=selected,
    )
    select.path = str(entry.path)


def _draw_gallery(layout, context, settings):
    image_state = context.window_manager.remi_image
    generation_state = context.window_manager.remi_generation
    entries = gallery.refresh(settings.output, image_state.refresh_token)
    header = layout.row(align=True)
    header.label(text=f"Images · {len(entries)}", icon="IMAGE_DATA")
    output = bpy.path.abspath(settings.output)
    if Path(output).is_dir():
        folder = header.operator("wm.path_open", text="", icon="FILE_FOLDER")
        folder.filepath = output
    header.operator("remi.refresh_images", text="", icon="FILE_REFRESH")
    if image_state.busy:
        box = layout.box()
        box.label(text=image_state.status or "Generating image…", icon="TIME")
        box.progress(
            factor=float(image_state.progress),
            type="BAR",
            text=f"{int(round(image_state.progress * 100))}%",
        )
    if not entries and not image_state.busy:
        _note(layout, "Generate an image above, or add an image for 3D.")
        return
    max_page = max(0, (len(entries) - 1) // PAGE_SIZE)
    image_state.page = min(image_state.page, max_page)
    start = image_state.page * PAGE_SIZE
    page = entries[start:start + PAGE_SIZE]
    grid = layout.column(align=True)
    for row_start in range(0, len(page), COLUMNS):
        row = grid.row(align=True)
        for entry in page[row_start:row_start + COLUMNS]:
            _draw_card(row, entry, _same_path(image_state.selected, entry.path)
                       if image_state.selected else False,
                       image_state, generation_state)
    if len(entries) > PAGE_SIZE:
        nav = layout.row(align=True)
        previous = nav.operator("remi.image_page", text="", icon="TRIA_LEFT")
        previous.direction = -1
        nav.label(text=f"{image_state.page + 1} / {max_page + 1}")
        following = nav.operator("remi.image_page", text="", icon="TRIA_RIGHT")
        following.direction = 1
    selected = _selected_entry(entries, image_state.selected)
    if selected is not None:
        box = layout.box()
        box.label(text="Selected", icon="IMAGE_DATA")
        for line in textwrap.wrap(selected.prompt, width=46):
            box.label(text=line)
        box.label(text=f"{selected.width} × {selected.height} · seed {selected.seed}")
        splat = splat_for_image(selected.path)
        if splat is not None:
            mesh = mesh_for_splat(splat)
            row = box.row(align=True)
            row.label(text="Mesh ready" if mesh else "3D ready", icon="CHECKMARK")
            activate = row.operator("remi.activate_splat", text="Select 3D", icon="RESTRICT_SELECT_OFF")
            activate.image_path = str(selected.path)
            if mesh is not None:
                row.operator("remi.select_workspace", text="", icon="MOD_DECIM").workspace = "OPTIMIZE"
                row.operator("remi.select_workspace", text="", icon="TEXTURE").workspace = "TEXTURE"
        else:
            mesh = None
            row = box.row()
            row.scale_y = 1.25
            play = row.operator("remi.generate_splats", text="Generate 3D", icon="PLAY")
            play.image_path = str(selected.path)
        actions = box.row(align=True)
        actions.operator("remi.regenerate_image", text="Load Settings", icon="FILE_REFRESH").path = str(selected.path)
        actions.operator("remi.copy_image_prompt", text="", icon="COPYDOWN").path = str(selected.path)
        actions.operator("remi.open_image", text="", icon="FILEBROWSER").path = str(selected.path)
        actions.operator("remi.delete_image", text="", icon="TRASH").path = str(selected.path)
        if splat is not None:
            active = context.view_layer.objects.active
            if active is splat or (mesh is not None and active is mesh):
                draw_mesh_controls(layout, context, generation_state)


def draw_create(layout, context):
    settings = context.scene.remi_image
    image_state = context.window_manager.remi_image
    generation_state = context.window_manager.remi_generation
    generation_settings = context.scene.remi_generation
    heading = layout.row(align=True)
    heading.label(text="Create", icon="IMAGE_DATA")
    heading.popover(panel="Remi_PT_ImageSettings", text="", icon="PREFERENCES")

    prompt = layout.box()
    prompt.label(text="Prompt", icon="TEXT")
    controls = prompt.column()
    controls.enabled = not image_state.busy
    controls.prop(settings, "prompt", text="")
    missing = missing_components(bpy.path.abspath(settings.models), settings.model)
    action = layout.row(align=True)
    action.scale_y = 1.35
    generate_row = action.row(align=True)
    generate_row.enabled = bool(settings.prompt.strip()) and not missing and not image_state.busy
    generate_row.operator("remi.generate_image", text="Generate Image", icon="PLAY")
    action.operator("remi.add_image", text="", icon="IMPORT")
    if image_state.busy:
        action.operator("remi.cancel_image", text="", icon="CANCEL")
    width, height = dimensions(settings)
    info = layout.row(align=True)
    info.label(text=f"{MODEL_LABELS[settings.model]} · {width}×{height} · {settings.count} image(s)", icon="INFO")
    if missing:
        warning = layout.box()
        warning.label(text=f"{MODEL_LABELS[settings.model]} is not installed", icon="INFO")
        size = 12 if settings.model == "base" else 6
        warning.label(text=f"Download about {size + 9} GB (model + Qwen3-VL text encoder)")
        warning.operator("remi.download_iris", text="Download Model", icon="IMPORT")
    elif not generation_settings.python:
        layout.label(text="Set the external CUDA/PyTorch Python in Create settings", icon="INFO")
    if image_state.status and not image_state.busy and image_state.status_kind != "SUCCESS":
        icon = {"ERROR": "ERROR", "CANCELLED": "CANCEL"}.get(image_state.status_kind, "INFO")
        _note(layout, image_state.status, icon=icon)
    if generation_state.status and not generation_state.busy and generation_state.status_kind == "ERROR":
        if not image_state.selected or _same_path(generation_state.source_image, image_state.selected):
            _note(layout, generation_state.status, icon="ERROR")
    layout.separator()
    _draw_gallery(layout, context, settings)


def draw_image_generation(layout, context):
    """Backward-compatible alias for the combined Create workspace."""
    draw_create(layout, context)


class Remi_PT_ImageSettings(Panel):
    bl_label = "Create Settings"
    bl_idname = "Remi_PT_ImageSettings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 20

    def draw(self, context):
        layout = self.layout
        settings = context.scene.remi_image
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.label(text="Image Generation", icon="IMAGE_DATA")
        controls = layout.column()
        controls.enabled = not context.window_manager.remi_image.busy
        controls.prop(settings, "model")
        controls.prop(settings, "size")
        controls.prop(settings, "count")
        controls.prop(settings, "steps")
        controls.prop(settings, "cfg_scale")
        controls.prop(settings, "seed")
        controls.prop(settings, "negative_prompt")
        controls.prop(settings, "models")
        controls.prop(settings, "output")
        controls.prop(settings, "memory_mode")
        controls.prop(settings, "cache_minutes")
        controls.operator("remi.download_iris", text="Download Image Model", icon="IMPORT")
        layout.separator()
        draw_generation_settings(layout, context)


classes = (Remi_PT_ImageSettings,)
