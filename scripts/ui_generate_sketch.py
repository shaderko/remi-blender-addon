"""Generate-workspace UI variants — throwaway design playground.

    scripts\\run-ui-sketch.bat   (blender --factory-startup --python scripts/ui_generate_sketch.py)

Registers three layouts for the Generate tab under sidebar category "GEN",
next to the add-on's own "Remi" category:

    A  Big cards       one full-width card per row; 3D starts from the card
    B  Two-up cards    two cards per row, same per-card action
    C  Hero + strip    one large preview, thumbnails below to switch

The premise: the thumbnail is the control point for 3D generation. Every card
carries its own primary action and its own progress, so making a 3D source never
requires going through a separate detail panel. Blender cannot make a scaled
preview itself clickable, so the action sits directly under the preview at full
card width — the closest idiom the panel API allows.

Panels read the real scene and gallery; buttons are the production operators.
Nothing here is imported by the add-on.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
from pathlib import Path

import bpy
from bpy.types import Panel

MODULE_NAME = "remi_generate_sketch"
BOOT = {"source": "not loaded", "error": ""}


def _script_location():
    raw = globals().get("__file__")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    return path if path.is_file() else None


FILE = _script_location()


def remi():
    return sys.modules["remi"]


def locate_root():
    override = os.environ.get("REMI_ROOT")
    if override:
        root = Path(override).resolve()
        if (root / "__init__.py").is_file():
            return root
    if FILE is not None:
        root = FILE.parents[1]
        if (root / "__init__.py").is_file() and (root / "app").is_dir():
            return root
    raise RuntimeError(
        "Cannot find the Remi checkout. Launch Blender with "
        "--python <repo>/scripts/ui_generate_sketch.py, or set REMI_ROOT."
    )


def registered_remi():
    for key in list(bpy.context.preferences.addons.keys()):
        name = key.lower()
        if name != "remi" and not name.endswith(".remi"):
            continue
        module = sys.modules.get(key)
        if module is not None and hasattr(module, "app"):
            return module
    return None


def load_remi():
    checkout = sys.modules.get("remi")
    if checkout is not None and hasattr(checkout, "app"):
        return "checkout"
    installed = registered_remi()
    if installed is not None:
        sys.modules["remi"] = installed
        return f"installed · {installed.__name__}"
    root = locate_root()
    spec = importlib.util.spec_from_file_location(
        "remi", root / "__init__.py", submodule_search_locations=[str(root)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = module
    spec.loader.exec_module(module)
    module.register()
    return "checkout"


def boot():
    try:
        BOOT["source"] = load_remi()
        BOOT["error"] = ""
    except Exception as exc:
        BOOT["source"] = "not loaded"
        BOOT["error"] = f"{type(exc).__name__}: {exc}"
        sys.modules.pop("remi", None)
    return BOOT


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


def panel_module():
    return sys.modules["remi.features.images.panel"]


def gallery():
    return sys.modules["remi.features.images.gallery"].gallery


def image_settings(context):
    return context.scene.remi_image


def image_state(context):
    return context.window_manager.remi_image


def generation_state(context):
    return context.window_manager.remi_generation


def entries(context):
    settings = image_settings(context)
    state = image_state(context)
    return gallery().refresh(settings.output, state.refresh_token)


def icon(path):
    return gallery().icon(path)


def same_path(first, second):
    return panel_module()._same_path(first, second)


def selected_entry(context, items):
    selected = image_state(context).selected
    if not selected:
        return None
    return next((entry for entry in items if same_path(selected, entry.path)), None)


def splat_for(path):
    return sys.modules["remi.blender.gaussian_splats"].splat_for_image(path)


def mesh_for(splat):
    return sys.modules["remi.blender.reconstructed_mesh"].mesh_for_splat(splat)


def model_missing(context):
    settings = image_settings(context)
    iris = sys.modules["remi.integrations.iris3b"]
    return iris.missing_components(bpy.path.abspath(settings.models), settings.model)


def dimensions(settings):
    return sys.modules["remi.features.images.settings"].dimensions(settings)


def ready(context):
    settings = image_settings(context)
    state = image_state(context)
    return bool(settings.prompt.strip()) and not model_missing(context) and not state.busy


def meta_line(entry):
    return f"seed {entry.seed}"


def state_of(context, entry):
    """(busy, splat, mesh) for one image — the card's own 3D state."""
    generation = generation_state(context)
    busy = bool(
        generation.busy
        and generation.source_image
        and same_path(generation.source_image, entry.path)
    )
    splat = splat_for(entry.path)
    mesh = mesh_for(splat) if splat is not None else None
    return busy, splat, mesh


# --------------------------------------------------------------------------
# Shared blocks
# --------------------------------------------------------------------------


def workspace_tabs(layout, context):
    sys.modules["remi.app.ui.workspace"].draw_workspace_tabs(layout, context)


def prompt_block(layout, context):
    settings = image_settings(context)
    state = image_state(context)
    box = layout.box()
    controls = box.column()
    controls.enabled = not state.busy
    controls.prop(settings, "prompt", text="")
    action = box.row(align=True)
    action.scale_y = 1.3
    primary = action.row(align=True)
    primary.enabled = ready(context)
    primary.operator("remi.generate_image", text="Generate Image", icon="PLAY")
    action.operator("remi.add_image", text="", icon="IMPORT")
    if state.busy:
        action.operator("remi.cancel_image", text="", icon="CANCEL")
    info = box.row(align=True)
    width, height = dimensions(settings)
    iris = sys.modules["remi.integrations.iris3b"]
    info.label(text=f"{iris.MODEL_LABELS[settings.model]} · {width}×{height} · {settings.count}")
    info.popover(panel="Remi_PT_ImageSettings", text="", icon="PREFERENCES")
    if state.busy:
        box.progress(
            factor=float(state.progress),
            type="BAR",
            text=f"{int(state.progress * 100)}%",
        )


def warnings(layout, context):
    state = image_state(context)
    if model_missing(context):
        iris = sys.modules["remi.integrations.iris3b"]
        box = layout.box()
        box.label(text=f"{iris.MODEL_LABELS[image_settings(context).model]} is not installed", icon="INFO")
        box.operator("remi.download_iris", text="Download Model", icon="IMPORT")
    if state.status and not state.busy and state.status_kind != "SUCCESS":
        icon_name = {"ERROR": "ERROR", "CANCELLED": "CANCEL"}.get(state.status_kind, "INFO")
        for index, line in enumerate(textwrap.wrap(state.status, 44)):
            layout.label(text=line, icon=icon_name if index == 0 else "NONE")


def gallery_header(layout, context, items):
    row = layout.row(align=True)
    row.label(text=f"Images · {len(items)}", icon="IMAGE_DATA")
    output = bpy.path.abspath(image_settings(context).output)
    if Path(output).is_dir():
        row.operator("wm.path_open", text="", icon="FILE_FOLDER").filepath = output
    row.operator("remi.refresh_images", text="", icon="FILE_REFRESH")


def card_actions(layout, context, entry, big):
    """The card's own 3D control: one primary, progress in place."""
    busy, splat, mesh = state_of(context, entry)
    generation = generation_state(context)
    if busy:
        layout.progress(
            factor=float(generation.progress),
            type="BAR",
            text=generation.status or "Generating 3D…",
        )
        layout.operator("remi.cancel_generation", text="Cancel", icon="CANCEL")
        return
    row = layout.row(align=True)
    row.scale_y = 1.45 if big else 1.2
    if splat is None:
        play = row.operator("remi.generate_splats", text="Generate 3D", icon="PLAY")
        play.image_path = str(entry.path)
    else:
        use = row.operator("remi.activate_splat", text="Select 3D", icon="RESTRICT_SELECT_OFF")
        use.image_path = str(entry.path)
        if mesh is not None:
            row.operator("remi.select_workspace", text="Optimize", icon="MOD_DECIM").workspace = "OPTIMIZE"


def card_tools(layout, context, entry):
    row = layout.row(align=True)
    row.operator("remi.regenerate_image", text="", icon="FILE_REFRESH").path = str(entry.path)
    row.operator("remi.copy_image_prompt", text="", icon="COPYDOWN").path = str(entry.path)
    row.operator("remi.open_image", text="", icon="FILEBROWSER").path = str(entry.path)
    row.operator("remi.delete_image", text="", icon="TRASH").path = str(entry.path)
    splat = splat_for(entry.path)
    if splat is not None and mesh_for(splat) is not None:
        row.operator("remi.select_workspace", text="", icon="TEXTURE").workspace = "TEXTURE"


def badge(layout, context, entry):
    _busy, splat, mesh = state_of(context, entry)
    if mesh is not None:
        layout.label(text="Mesh", icon="CHECKMARK")
    elif splat is not None:
        layout.label(text="3D", icon="CHECKMARK")
    else:
        layout.label(text="", icon="NONE")


def deck_card(layout, context, entry, selected, scale, big):
    box = layout.box()
    box.alert = selected
    preview = icon(entry.path)
    if preview:
        box.template_icon(icon_value=preview, scale=scale)
    else:
        box.label(text="Preview", icon="IMAGE_DATA")
    head = box.row(align=True)
    select = head.operator(
        "remi.select_image",
        text="",
        icon="RADIOBUT_ON" if selected else "RADIOBUT_OFF",
        depress=selected,
    )
    select.path = str(entry.path)
    head.label(text=meta_line(entry))
    badge(head, context, entry)
    if big:
        box.label(text=entry.label)
    card_actions(box, context, entry, big)
    if big:
        card_tools(box, context, entry)


def deck(layout, context, items, columns, scale, page_size):
    state = image_state(context)
    if not items:
        layout.label(text="No images yet — generate one above.", icon="INFO")
        return
    max_page = max(0, (len(items) - 1) // page_size)
    state.page = min(state.page, max_page)
    start = state.page * page_size
    page = items[start:start + page_size]
    column = layout.column(align=True)
    for offset in range(0, len(page), columns):
        row = column.row(align=True)
        for entry in page[offset:offset + columns]:
            selected = same_path(state.selected, entry.path) if state.selected else False
            deck_card(row, context, entry, selected, scale, columns == 1)
    if len(items) > page_size:
        nav = layout.row(align=True)
        previous = nav.operator("remi.image_page", text="", icon="TRIA_LEFT")
        previous.direction = -1
        nav.label(text=f"{state.page + 1} / {max_page + 1}")
        following = nav.operator("remi.image_page", text="", icon="TRIA_RIGHT")
        following.direction = 1


def result_block(layout, context):
    module = sys.modules["remi.features.generation.panel"]
    module.draw_mesh_controls(layout, context, generation_state(context))


# --------------------------------------------------------------------------
# A — big cards
# --------------------------------------------------------------------------


def variant_big(layout, context):
    workspace_tabs(layout, context)
    prompt_block(layout, context)
    warnings(layout, context)
    layout.separator()
    items = entries(context)
    gallery_header(layout, context, items)
    deck(layout, context, items, columns=1, scale=7.0, page_size=3)
    result_block(layout, context)


# --------------------------------------------------------------------------
# B — two-up cards
# --------------------------------------------------------------------------


def variant_two_up(layout, context):
    workspace_tabs(layout, context)
    prompt_block(layout, context)
    warnings(layout, context)
    layout.separator()
    items = entries(context)
    gallery_header(layout, context, items)
    deck(layout, context, items, columns=2, scale=3.2, page_size=4)
    result_block(layout, context)


# --------------------------------------------------------------------------
# C — hero + strip
# --------------------------------------------------------------------------


def variant_hero(layout, context):
    workspace_tabs(layout, context)
    prompt_block(layout, context)
    warnings(layout, context)

    items = entries(context)
    if not items:
        layout.label(text="No images yet — generate one above.", icon="INFO")
        return
    chosen = selected_entry(context, items) or items[0]

    card = layout.box()
    preview = icon(chosen.path)
    if preview:
        card.template_icon(icon_value=preview, scale=7.0)
    head = card.row(align=True)
    head.label(text=meta_line(chosen), icon="IMAGE_DATA")
    badge(head, context, chosen)
    card_actions(card, context, chosen, big=True)
    card_tools(card, context, chosen)

    layout.separator()
    layout.label(text="All images", icon="IMAGE_DATA")
    strip = layout.column(align=True)
    for offset in range(0, len(items), 4):
        row = strip.row(align=True)
        for entry in items[offset:offset + 4]:
            cell = row.column(align=True)
            preview = icon(entry.path)
            if preview:
                cell.template_icon(icon_value=preview, scale=1.6)
            select = cell.operator(
                "remi.select_image",
                text="",
                icon="RADIOBUT_ON" if same_path(chosen.path, entry.path) else "RADIOBUT_OFF",
            )
            select.path = str(entry.path)
            _busy, splat, _mesh = state_of(context, entry)
            if splat is not None:
                cell.label(text="3D", icon="CHECKMARK")
    result_block(layout, context)


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------


def guarded(draw):
    def wrapper(panel, context):
        layout = panel.layout
        try:
            draw(layout, context)
        except Exception as exc:
            layout.label(text=f"{type(exc).__name__}: {exc}", icon="ERROR")

    return wrapper


class GEN_PT_Control(Panel):
    bl_label = "Generate sketches"
    bl_idname = "GEN_PT_Control"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"

    def draw(self, _context):
        layout = self.layout
        layout.label(
            text=f"Remi · {BOOT['source']}",
            icon="CHECKMARK" if BOOT["source"] != "not loaded" else "ERROR",
        )
        if BOOT["error"]:
            for line in textwrap.wrap(BOOT["error"], 42):
                layout.label(text=line, icon="ERROR")
        layout.label(text="Live panels: real operators.", icon="INFO")


class GEN_PT_Big(Panel):
    bl_label = "A · Big cards"
    bl_idname = "GEN_PT_Big"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    draw = guarded(variant_big)


class GEN_PT_TwoUp(Panel):
    bl_label = "B · Two-up cards"
    bl_idname = "GEN_PT_TwoUp"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_two_up)


class GEN_PT_Hero(Panel):
    bl_label = "C · Hero + strip"
    bl_idname = "GEN_PT_Hero"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_hero)


CLASSES = (GEN_PT_Control, GEN_PT_Big, GEN_PT_TwoUp, GEN_PT_Hero)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def main():
    register()
    boot()
    print(f"Remi generate sketch ready: N-panel → GEN tab. Remi source: {BOOT['source']}")
    if BOOT["error"]:
        print(f"Could not load Remi: {BOOT['error']}")


if __name__ == "__main__":
    main()
