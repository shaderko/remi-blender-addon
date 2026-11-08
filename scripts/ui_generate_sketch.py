"""Generate-workspace UI variants — a throwaway design playground.

    scripts\\run-ui-sketch.bat  (or: blender --factory-startup --python scripts/ui_generate_sketch.py)

Registers four alternative layouts for the Generate tab under the sidebar
category "GEN", next to the add-on's own "Remi" category, so you can compare
them against the current one:

    1  Prompt + grid       prompt bar, 3-column thumbnails, one action bar
    2  Three steps         Prompt / Pick / Make 3D as numbered sections
    3  Filmstrip           one large preview, thumbnails as a strip below
    4  Browse sheet        compact sidebar, wide floating browser panel

These panels read the real scene and the real gallery on disk; the buttons are
the production operators, so a variant can actually drive the flow. Nothing in
this file is imported by the add-on.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
from pathlib import Path

import bpy
from bpy.types import Operator, Panel

MODULE_NAME = "remi_generate_sketch"
BOOT = {"source": "not loaded", "error": ""}
GRID_COLUMNS = 3


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
    return f"{entry.model_label} · seed {entry.seed} · {entry.width}×{entry.height}"


# --------------------------------------------------------------------------
# Shared blocks
# --------------------------------------------------------------------------


def workspace_tabs(layout, context):
    sys.modules["remi.app.ui.workspace"].draw_workspace_tabs(layout, context)


def prompt_block(layout, context):
    settings = image_settings(context)
    state = image_state(context)
    box = layout.box()
    box.label(text="Prompt", icon="TEXT")
    controls = box.column()
    controls.enabled = not state.busy
    controls.prop(settings, "prompt", text="")
    action = box.row(align=True)
    action.scale_y = 1.35
    primary = action.row(align=True)
    primary.enabled = ready(context)
    primary.operator("remi.generate_image", text="Generate", icon="PLAY")
    action.operator("remi.add_image", text="", icon="IMPORT")
    if state.busy:
        action.operator("remi.cancel_image", text="", icon="CANCEL")
    info = box.row(align=True)
    width, height = dimensions(settings)
    iris = sys.modules["remi.integrations.iris3b"]
    info.label(text=f"{iris.MODEL_LABELS[settings.model]} · {width}×{height} · {settings.count}")
    info.popover(panel="Remi_PT_ImageSettings", text="", icon="PREFERENCES")


def busy_block(layout, context):
    state = image_state(context)
    generation = generation_state(context)
    if state.busy:
        box = layout.box()
        box.label(text=state.status or "Generating image…", icon="TIME")
        box.progress(factor=float(state.progress), type="BAR", text=f"{int(state.progress * 100)}%")
    if generation.mesh_busy:
        box = layout.box()
        box.label(text=generation.mesh_status or "Creating mesh…", icon="TIME")


def warnings(layout, context):
    state = image_state(context)
    missing = model_missing(context)
    if missing:
        box = layout.box()
        iris = sys.modules["remi.integrations.iris3b"]
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


def card(layout, entry, selected, scale=2.2, label=True):
    box = layout.box()
    box.alert = selected
    preview = icon(entry.path)
    if preview:
        box.template_icon(icon_value=preview, scale=scale)
    else:
        box.label(text="Preview", icon="IMAGE_DATA")
    row = box.row(align=True)
    select = row.operator(
        "remi.select_image",
        text="" if label else "Select",
        icon="RADIOBUT_ON" if selected else "RADIOBUT_OFF",
        depress=selected,
    )
    select.path = str(entry.path)
    if splat_for(entry.path) is not None:
        row.label(text="3D", icon="CHECKMARK")


def grid(layout, context, items, columns=GRID_COLUMNS, scale=2.2, paging=True):
    state = image_state(context)
    page_size = 6 if paging else len(items)
    if not items:
        layout.label(text="No images yet — generate one, or add a file.", icon="INFO")
        return
    max_page = max(0, (len(items) - 1) // page_size)
    state.page = min(state.page, max_page)
    start = state.page * page_size
    page = items[start:start + page_size]
    column = layout.column(align=True)
    for offset in range(0, len(page), columns):
        row = column.row(align=True)
        for entry in page[offset:offset + columns]:
            card(row, entry, same_path(state.selected, entry.path) if state.selected else False, scale)
    if paging and len(items) > page_size:
        nav = layout.row(align=True)
        previous = nav.operator("remi.image_page", text="", icon="TRIA_LEFT")
        previous.direction = -1
        nav.label(text=f"{state.page + 1} / {max_page + 1}")
        following = nav.operator("remi.image_page", text="", icon="TRIA_RIGHT")
        following.direction = 1


def detail_block(layout, context, entry):
    box = layout.box()
    box.label(text=meta_line(entry), icon="IMAGE_DATA")
    for line in textwrap.wrap(entry.prompt, 44)[:2]:
        box.label(text=line)
    splat = splat_for(entry.path)
    row = box.row(align=True)
    row.scale_y = 1.25
    if splat is None:
        play = row.operator("remi.generate_splats", text="Generate 3D", icon="PLAY")
        play.image_path = str(entry.path)
    else:
        use = row.operator("remi.activate_splat", text="Select 3D", icon="RESTRICT_SELECT_OFF")
        use.image_path = str(entry.path)
        mesh = mesh_for(splat)
        if mesh is not None:
            row.operator("remi.select_workspace", text="", icon="MOD_DECIM").workspace = "OPTIMIZE"
            row.operator("remi.select_workspace", text="", icon="TEXTURE").workspace = "TEXTURE"
    actions = box.row(align=True)
    actions.operator("remi.regenerate_image", text="Load Settings", icon="FILE_REFRESH").path = str(entry.path)
    actions.operator("remi.copy_image_prompt", text="", icon="COPYDOWN").path = str(entry.path)
    actions.operator("remi.open_image", text="", icon="FILEBROWSER").path = str(entry.path)
    actions.operator("remi.delete_image", text="", icon="TRASH").path = str(entry.path)


def result_block(layout, context):
    """The 3D result controls are borrowed from the production panel."""
    module = sys.modules["remi.features.generation.panel"]
    module.draw_mesh_controls(layout, context, generation_state(context))


# --------------------------------------------------------------------------
# Variant 1 — prompt + grid
# --------------------------------------------------------------------------


def variant_one(layout, context):
    workspace_tabs(layout, context)
    prompt_block(layout, context)
    busy_block(layout, context)
    warnings(layout, context)
    layout.separator()
    items = entries(context)
    gallery_header(layout, context, items)
    grid(layout, context, items)
    chosen = selected_entry(context, items)
    if chosen is not None:
        layout.separator()
        detail_block(layout, context, chosen)
    result_block(layout, context)


# --------------------------------------------------------------------------
# Variant 2 — three numbered steps
# --------------------------------------------------------------------------


def variant_two(layout, context):
    workspace_tabs(layout, context)
    items = entries(context)
    chosen = selected_entry(context, items)

    step = layout.box()
    step.label(text="1 · Describe the image", icon="TEXT")
    prompt_block(step, context)

    step = layout.box()
    step.label(text="2 · Pick an image", icon="IMAGE_DATA")
    gallery_header(step, context, items)
    grid(step, context, items, columns=2, scale=2.6)

    step = layout.box()
    step.label(text="3 · Make a 3D source", icon="OUTLINER_OB_POINTCLOUD")
    if chosen is None:
        step.label(text="Select an image above to continue", icon="INFO")
    else:
        detail_block(step, context, chosen)
        result_block(step, context)
    busy_block(layout, context)
    warnings(layout, context)


# --------------------------------------------------------------------------
# Variant 3 — filmstrip
# --------------------------------------------------------------------------


def variant_three(layout, context):
    workspace_tabs(layout, context)
    prompt_block(layout, context)
    busy_block(layout, context)
    warnings(layout, context)

    items = entries(context)
    chosen = selected_entry(context, items)
    gallery_header(layout, context, items)

    box = layout.box()
    if chosen is None:
        box.label(text="No image selected", icon="IMAGE_DATA")
    else:
        preview = icon(chosen.path)
        if preview:
            box.template_icon(icon_value=preview, scale=4.0)
        box.label(text=meta_line(chosen), icon="IMAGE_DATA")

    if items:
        strip = layout.column(align=True)
        for offset in range(0, len(items), 5):
            row = strip.row(align=True)
            for entry in items[offset:offset + 5]:
                cell = row.column(align=True)
                preview = icon(entry.path)
                if preview:
                    cell.template_icon(icon_value=preview, scale=0.6)
                select = cell.operator(
                    "remi.select_image",
                    text="",
                    icon="RADIOBUT_ON" if chosen is not None and same_path(chosen.path, entry.path) else "RADIOBUT_OFF",
                )
                select.path = str(entry.path)

    if chosen is not None:
        detail_block(layout, context, chosen)
    result_block(layout, context)


# --------------------------------------------------------------------------
# Variant 4 — compact sidebar + wide browser sheet
# --------------------------------------------------------------------------


def variant_four(layout, context):
    workspace_tabs(layout, context)
    prompt_block(layout, context)
    busy_block(layout, context)
    warnings(layout, context)

    items = entries(context)
    chosen = selected_entry(context, items)
    row = layout.row(align=True)
    row.label(text=f"{len(items)} image{'s' if len(items) != 1 else ''}", icon="IMAGE_DATA")
    row.popover(panel="GEN_PT_Browser", text="Browse", icon="WINDOW")

    layout.separator()
    if chosen is None and items:
        layout.label(text="Nothing selected yet", icon="INFO")
    elif chosen is not None:
        detail_block(layout, context, chosen)
    result_block(layout, context)


class GEN_PT_Browser(Panel):
    bl_label = "Image Browser"
    bl_idname = "GEN_PT_Browser"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 22

    def draw(self, context):
        layout = self.layout
        try:
            items = entries(context)
            chosen = selected_entry(context, items)
            split = layout.split(factor=0.62)
            left = split.column()
            right = split.column()
            if not items:
                left.label(text="No images yet", icon="INFO")
            for offset in range(0, len(items), 4):
                row = left.row(align=True)
                for entry in items[offset:offset + 4]:
                    cell = row.column(align=True)
                    preview = icon(entry.path)
                    if preview:
                        cell.template_icon(icon_value=preview, scale=1.4)
                    select = cell.operator(
                        "remi.select_image",
                        text="",
                        icon="RADIOBUT_ON" if chosen is not None and same_path(chosen.path, entry.path) else "RADIOBUT_OFF",
                    )
                    select.path = str(entry.path)
            if chosen is not None:
                detail_block(right, context, chosen)
            else:
                right.label(text="Pick an image on the left", icon="INFO")
            layout.separator()
            layout.operator("remi.refresh_images", text="Refresh", icon="FILE_REFRESH")
        except Exception as exc:
            layout.label(text=f"{type(exc).__name__}: {exc}", icon="ERROR")


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
        layout.label(text="Panels below are live: real operators.", icon="INFO")
        layout.label(text="Compare with the Remi tab above.")


class GEN_PT_One(Panel):
    bl_label = "1 · Prompt + grid"
    bl_idname = "GEN_PT_One"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    draw = guarded(variant_one)


class GEN_PT_Two(Panel):
    bl_label = "2 · Three steps"
    bl_idname = "GEN_PT_Two"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_two)


class GEN_PT_Three(Panel):
    bl_label = "3 · Filmstrip"
    bl_idname = "GEN_PT_Three"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_three)


class GEN_PT_Four(Panel):
    bl_label = "4 · Browse sheet"
    bl_idname = "GEN_PT_Four"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_four)


CLASSES = (
    GEN_PT_Browser,
    GEN_PT_Control,
    GEN_PT_One,
    GEN_PT_Two,
    GEN_PT_Three,
    GEN_PT_Four,
)


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
