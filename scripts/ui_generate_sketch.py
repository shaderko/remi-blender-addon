"""Generate-workspace UI variants — throwaway design playground.

    scripts\\run-ui-sketch.bat   (blender --factory-startup --python scripts/ui_generate_sketch.py)

Registers four layouts for the Generate tab under sidebar category "GEN":

    A  1-up       one image per row, ~240 px
    B  2-up       two images per row, ~140 px at the default sidebar width
    C  3-up       three per row, ~90 px — the densest overview
    D  2-up wide  two per row at ~230 px — drag the sidebar to ~500 px

The rule for all of them: the cell is the image. No boxes, no seed line, no
prompt text, no tool rows inside the cell — just the preview and one slim button
row underneath (its own 3D action plus a compact menu for the rest). Everything
else lives once, above the grid.

Blender cannot make a scaled preview clickable, and a preview does not grow with
the panel, so the image size is `scale` × ~32 px and the column count must match
the panel width you actually use.

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
from bpy.props import StringProperty
from bpy.types import Menu, Operator, Panel

MODULE_NAME = "remi_generate_sketch"
BOOT = {"source": "not loaded", "error": ""}
MENU_PATH = "gen_cell_path"


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
    return sys.modules["remi.features.images.panel"]._same_path(first, second)


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


def state_of(context, entry):
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


def result_block(layout, context):
    module = sys.modules["remi.features.generation.panel"]
    module.draw_mesh_controls(layout, context, generation_state(context))


# --------------------------------------------------------------------------
# The cell: image, then one slim button row
# --------------------------------------------------------------------------


def image_cell(row, context, entry, scale, label="3D"):
    cell = row.column(align=True)
    preview = icon(entry.path)
    if preview:
        cell.template_icon(icon_value=preview, scale=scale)
    else:
        cell.label(text="", icon="IMAGE_DATA")

    busy, splat, _mesh = state_of(context, entry)
    generation = generation_state(context)
    if busy:
        cell.progress(
            factor=float(generation.progress),
            type="BAR",
            text="",
        )
        cell.operator("remi.cancel_generation", text="", icon="CANCEL")
        return

    row = cell.row(align=True)
    wide = scale >= 6.0
    split = row.split(factor=0.72 if wide else 0.82)
    if splat is None:
        play = split.operator("remi.generate_splats", text=label, icon="PLAY")
        play.image_path = str(entry.path)
    else:
        use = split.operator("remi.activate_splat", text="3D", icon="CHECKMARK")
        use.image_path = str(entry.path)
    if wide:
        zoom = split.operator("gen.open_full", text="", icon="ZOOM_IN")
        zoom.path = str(entry.path)
    more = split.operator("gen.cell_menu", text="", icon="COLLAPSEMENU")
    more.path = str(entry.path)


def image_grid(layout, context, items, columns, scale, page_size, label="3D"):
    state = image_state(context)
    if not items:
        layout.label(text="No images yet — generate one above.", icon="INFO")
        return
    max_page = max(0, (len(items) - 1) // page_size)
    state.page = min(state.page, max_page)
    start = state.page * page_size
    page = items[start:start + page_size]
    grid = layout.column(align=True)
    for offset in range(0, len(page), columns):
        row = grid.row(align=True)
        for entry in page[offset:offset + columns]:
            image_cell(row, context, entry, scale, label)
    if len(items) > page_size:
        nav = layout.row(align=True)
        previous = nav.operator("remi.image_page", text="", icon="TRIA_LEFT")
        previous.direction = -1
        nav.label(text=f"{state.page + 1} / {max_page + 1}")
        following = nav.operator("remi.image_page", text="", icon="TRIA_RIGHT")
        following.direction = 1


# --------------------------------------------------------------------------
# Per-cell menu (secondary actions without per-cell chrome)
# --------------------------------------------------------------------------


class GEN_OT_CellMenu(Operator):
    bl_idname = "gen.cell_menu"
    bl_label = "Image Actions"
    bl_options = {"INTERNAL"}

    path: StringProperty()

    def execute(self, context):
        context.scene[MENU_PATH] = self.path
        bpy.ops.wm.call_menu(name="GEN_MT_Cell")
        return {"FINISHED"}


class GEN_OT_OpenFull(Operator):
    """Show the image at full size in an Image Editor, inside this Blender."""

    bl_idname = "gen.open_full"
    bl_label = "Open Full Size"
    bl_description = "Open this image in an Image Editor inside Blender"
    bl_options = {"INTERNAL"}

    path: StringProperty()

    def execute(self, context):
        path = bpy.path.abspath(self.path)
        if not Path(path).is_file():
            self.report({"ERROR"}, "Image file is missing")
            return {"CANCELLED"}
        image = bpy.data.images.load(path, check_existing=True)
        window = context.window
        if window is None:
            self.report({"ERROR"}, "No window to open the image in")
            return {"CANCELLED"}
        screen = window.screen

        area = next((item for item in screen.areas if item.type == "IMAGE_EDITOR"), None)
        if area is None:
            area = self._split_area(context, window, screen)
        if area is None:
            self.report({"ERROR"}, "Could not open an Image Editor")
            return {"CANCELLED"}

        area.type = "IMAGE_EDITOR"
        for space in area.spaces:
            if space.type == "IMAGE_EDITOR":
                space.image = image
                space.show_region_toolbar = False
                break
        self.report({"INFO"}, f"Opened {Path(path).name}")
        return {"FINISHED"}

    @staticmethod
    def _split_area(context, window, screen):
        source = context.area
        if source is None or source.type == "IMAGE_EDITOR":
            source = max(screen.areas, key=lambda item: item.width * item.height)
        before = {item.as_pointer() for item in screen.areas}
        try:
            with context.temp_override(window=window, screen=screen, area=source):
                bpy.ops.screen.area_split(direction="HORIZONTAL", factor=0.5)
        except Exception:
            return None
        return next(
            (item for item in screen.areas if item.as_pointer() not in before),
            None,
        )


class GEN_MT_Cell(Menu):
    bl_idname = "GEN_MT_Cell"
    bl_label = "Image"

    def draw(self, context):
        layout = self.layout
        path = context.scene.get(MENU_PATH, "")
        if not path:
            layout.label(text="No image", icon="INFO")
            return
        splat = splat_for(path)
        if splat is None:
            play = layout.operator("remi.generate_splats", text="Generate 3D", icon="PLAY")
            play.image_path = path
        else:
            use = layout.operator("remi.activate_splat", text="Select 3D", icon="RESTRICT_SELECT_OFF")
            use.image_path = path
            mesh = mesh_for(splat)
            if mesh is not None:
                layout.operator("remi.select_workspace", text="Optimize", icon="MOD_DECIM").workspace = "OPTIMIZE"
                layout.operator("remi.select_workspace", text="Texture", icon="TEXTURE").workspace = "TEXTURE"
        layout.separator()
        layout.operator("gen.open_full", text="Open Full Size", icon="ZOOM_IN").path = path
        layout.operator("remi.regenerate_image", text="Load Settings", icon="FILE_REFRESH").path = path
        layout.operator("remi.copy_image_prompt", text="Copy Prompt", icon="COPYDOWN").path = path
        layout.operator("remi.open_image", text="Open Image", icon="FILEBROWSER").path = path
        layout.operator("remi.delete_image", text="Delete", icon="TRASH").path = path


# --------------------------------------------------------------------------
# Variants
# --------------------------------------------------------------------------


def common_top(layout, context):
    workspace_tabs(layout, context)
    prompt_block(layout, context)
    warnings(layout, context)


def variant_one_up(layout, context):
    common_top(layout, context)
    layout.separator()
    items = entries(context)
    gallery_header(layout, context, items)
    image_grid(layout, context, items, columns=1, scale=7.0, page_size=3, label="Generate 3D")
    result_block(layout, context)


def variant_two_up(layout, context):
    common_top(layout, context)
    layout.separator()
    items = entries(context)
    gallery_header(layout, context, items)
    image_grid(layout, context, items, columns=2, scale=3.8, page_size=4)
    result_block(layout, context)


def variant_three_up(layout, context):
    common_top(layout, context)
    layout.separator()
    items = entries(context)
    gallery_header(layout, context, items)
    image_grid(layout, context, items, columns=3, scale=2.8, page_size=6)
    result_block(layout, context)


def variant_wide(layout, context):
    common_top(layout, context)
    layout.label(text="Drag the sidebar to ~500 px for this one", icon="INFO")
    layout.separator()
    items = entries(context)
    gallery_header(layout, context, items)
    image_grid(layout, context, items, columns=2, scale=6.5, page_size=2)
    result_block(layout, context)


def variant_adaptive(layout, context):
    """One column when narrow, more as the sidebar widens — image size is fixed."""
    common_top(layout, context)
    layout.separator()
    items = entries(context)
    gallery_header(layout, context, items)
    if not items:
        layout.label(text="No images yet — generate one above.", icon="INFO")
        return
    state = image_state(context)
    page_size = 6
    max_page = max(0, (len(items) - 1) // page_size)
    state.page = min(state.page, max_page)
    start = state.page * page_size
    page = items[start:start + page_size]
    flow = layout.grid_flow(
        row_major=True, columns=0, even_columns=True, even_rows=True, align=True
    )
    for entry in page:
        image_cell(flow, context, entry, 8.0, "3D")
    if len(items) > page_size:
        nav = layout.row(align=True)
        previous = nav.operator("remi.image_page", text="", icon="TRIA_LEFT")
        previous.direction = -1
        nav.label(text=f"{state.page + 1} / {max_page + 1}")
        following = nav.operator("remi.image_page", text="", icon="TRIA_RIGHT")
        following.direction = 1
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
        layout.label(text="Image size is the `scale=` number.", icon="INFO")


class GEN_PT_OneUp(Panel):
    bl_label = "A · 1-up (240 px)"
    bl_idname = "GEN_PT_OneUp"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_one_up)


class GEN_PT_TwoUp(Panel):
    bl_label = "B · 2-up (140 px)"
    bl_idname = "GEN_PT_TwoUp"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    draw = guarded(variant_two_up)


class GEN_PT_ThreeUp(Panel):
    bl_label = "C · 3-up (90 px)"
    bl_idname = "GEN_PT_ThreeUp"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_three_up)


class GEN_PT_Wide(Panel):
    bl_label = "D · 2-up wide (208 px)"
    bl_idname = "GEN_PT_Wide"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_wide)


class GEN_PT_Adaptive(Panel):
    bl_label = "E · Adaptive (256 px)"
    bl_idname = "GEN_PT_Adaptive"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(variant_adaptive)


CLASSES = (
    GEN_OT_CellMenu,
    GEN_OT_OpenFull,
    GEN_MT_Cell,
    GEN_PT_Control,
    GEN_PT_OneUp,
    GEN_PT_TwoUp,
    GEN_PT_ThreeUp,
    GEN_PT_Wide,
    GEN_PT_Adaptive,
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


def warm_icons():
    """Queue previews before the first draw; drawing cannot wait for them."""
    try:
        for entry in entries(bpy.context):
            icon(entry.path)
    except Exception as exc:
        print(f"Remi sketch: preview warm-up skipped: {exc}")


def main():
    register()
    boot()
    warm_icons()
    print(f"Remi generate sketch ready: N-panel → GEN tab. Remi source: {BOOT['source']}")
    if BOOT["error"]:
        print(f"Could not load Remi: {BOOT['error']}")


if __name__ == "__main__":
    main()
