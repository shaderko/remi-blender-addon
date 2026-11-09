"""Image board — GPU canvas in its own window, no scene content.

    scripts\\run-ui-sketch.bat

Feels like a node canvas, without nodes:

- background: dark surface with a dot grid that follows zoom and pan
- cards: draggable images, raised on click, multi-select with shift
- wheel zooms around the cursor, LMB-drag on empty space pans, MMB pans anywhere
- double-click opens the image full size, right-click opens the board menu

Images are loaded as datablocks for texturing only; nothing enters the scene and
the datablocks are removed when the board closes.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import time
from pathlib import Path

import blf
import bpy
import gpu
from bpy.props import StringProperty
from bpy.types import Menu, Operator, Panel
from gpu_extras.batch import batch_for_shader

BOOT = {"source": "not loaded", "error": ""}
SELECTION_KEY = "remi_board_selection"

CARD_W = 300.0
CARD_H = 280.0
GAP_X = 40.0
GAP_Y = 48.0
COLUMNS = 4
THUMB = 512

BG = (0.152, 0.152, 0.158, 1.0)
DOT = (0.235, 0.235, 0.245, 1.0)
CARD = (0.205, 0.205, 0.215, 1.0)
CARD_HOT = (0.235, 0.235, 0.245, 1.0)
CARD_SEL = (0.30, 0.22, 0.15, 1.0)
TEXT = (0.86, 0.86, 0.9, 1.0)
DIM = (0.55, 0.55, 0.6, 1.0)
ACCENT = (1.0, 0.55, 0.15, 1.0)
HUD = (0.0, 0.0, 0.0, 0.55)

BOARD = None


def _script_location():
    raw = globals().get("__file__")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve() if path.is_file() else None


FILE = _script_location()


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
    raise RuntimeError("Cannot find the Remi checkout; set REMI_ROOT or launch with --python.")


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


def gallery_entries(context):
    if sys.modules.get("remi") is None:
        return []
    try:
        gallery = sys.modules["remi.features.images.gallery"].gallery
        settings = context.scene.remi_image
        state = context.window_manager.remi_image
        return gallery.refresh(settings.output, state.refresh_token)
    except Exception:
        return []


# --------------------------------------------------------------------------
# Shaders
# --------------------------------------------------------------------------


def _shader(names):
    for name in names:
        try:
            return gpu.shader.from_builtin(name)
        except Exception:
            continue
    raise RuntimeError(f"no shader among {names}")


_IMAGE = None
_FLAT = None


def image_shader():
    global _IMAGE
    if _IMAGE is None:
        _IMAGE = _shader(("IMAGE", "2D_IMAGE"))
    return _IMAGE


def flat_shader():
    global _FLAT
    if _FLAT is None:
        _FLAT = _shader(("UNIFORM_COLOR", "2D_FLAT_COLOR"))
    return _FLAT


def draw_rect(x, y, w, h, colour):
    shader = flat_shader()
    shader.uniform_float("color", colour)
    batch = batch_for_shader(
        shader, "TRI_FAN", {"pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]}
    )
    batch.draw(shader)


def draw_rects(rects, colour):
    if not rects:
        return
    shader = flat_shader()
    shader.uniform_float("color", colour)
    vertices = []
    for x, y, w, h in rects:
        vertices.extend(
            [(x, y), (x + w, y), (x + w, y + h), (x, y), (x + w, y + h), (x, y + h)]
        )
    batch = batch_for_shader(shader, "TRIS", {"pos": vertices})
    batch.draw(shader)


def draw_image(texture, x, y, w, h):
    shader = image_shader()
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch = batch_for_shader(
        shader,
        "TRI_FAN",
        {
            "pos": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
            "texCoord": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        },
    )
    batch.draw(shader)


def draw_text(text, x, y, size=12, colour=TEXT):
    font = 0
    blf.size(font, int(max(8, size)))
    blf.color(font, *colour)
    blf.position(font, x, y, 0)
    blf.draw(font, text)


# --------------------------------------------------------------------------
# Board state
# --------------------------------------------------------------------------


class Board:
    def __init__(self, window):
        self.window = window.as_pointer()
        self.zoom = 0.7
        self.pan = [90.0, 640.0]
        self.entries = []
        self.order = []
        self.positions = {}
        self.selected = set()
        self.textures = {}
        self.datablocks = []
        self.hover = None
        self.region = (1440, 810)
        self.pan_drag = None
        self.card_drag = None
        self.last_click = (None, 0.0)

    # layout -------------------------------------------------------------
    def load(self, entries):
        self.entries = entries
        self.order = [str(entry.path) for entry in entries]
        for index, entry in enumerate(entries):
            column = index % COLUMNS
            row = index // COLUMNS
            self.positions[str(entry.path)] = [
                column * (CARD_W + GAP_X),
                -(row * (CARD_H + GAP_Y)),
            ]

    def card_rect(self, path):
        x, y = self.positions.get(path, (0.0, 0.0))
        sx = x * self.zoom + self.pan[0]
        sy = y * self.zoom + self.pan[1]
        return sx, sy, CARD_W * self.zoom, CARD_H * self.zoom

    def hit(self, mx, my):
        for path in reversed(self.order):
            x, y, w, h = self.card_rect(path)
            if x <= mx <= x + w and y <= my <= y + h:
                return path
        return None

    def raise_card(self, path):
        if path in self.order:
            self.order.remove(path)
            self.order.append(path)

    def move_selected(self, path, dx, dy):
        targets = self.selected if path in self.selected and len(self.selected) > 1 else {path}
        for target in targets:
            position = self.positions.get(target)
            if position is not None:
                position[0] += dx
                position[1] += dy

    def fit(self):
        if not self.order:
            return
        xs = [self.positions[p][0] for p in self.order]
        ys = [self.positions[p][1] for p in self.order]
        left, right = min(xs), max(xs) + CARD_W
        top, bottom = max(ys) + CARD_H, min(ys)
        width, height = self.region
        board_w = max(1.0, right - left)
        board_h = max(1.0, top - bottom)
        self.zoom = max(0.12, min(width / (board_w + 120), height / (board_h + 140), 1.4))
        self.pan[0] = (width - board_w * self.zoom) / 2 - left * self.zoom
        self.pan[1] = (height - board_h * self.zoom) / 2 - bottom * self.zoom
        self.pan[1] -= 20

    # textures -----------------------------------------------------------
    def texture(self, path):
        cached = self.textures.get(path)
        if cached is not None:
            return cached
        try:
            image = bpy.data.images.load(path, check_existing=False)
            image.scale(THUMB, THUMB)
            texture = gpu.texture.from_image(image)
        except Exception:
            self.textures[path] = None
            return None
        self.datablocks.append(image)
        self.textures[path] = texture
        return texture

    def cleanup(self):
        for image in self.datablocks:
            try:
                bpy.data.images.remove(image)
            except Exception:
                pass
        self.datablocks.clear()
        self.textures.clear()


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------


def draw_grid(board, width, height):
    spacing = 26.0 * board.zoom
    while spacing < 14.0:
        spacing *= 2.0
    while spacing > 90.0:
        spacing /= 2.0
    start_x = board.pan[0] % spacing
    start_y = board.pan[1] % spacing
    size = 2.0 if board.zoom > 0.5 else 1.5
    dots = []
    y = start_y
    while y < height:
        x = start_x
        while x < width:
            dots.append((x, y, size, size))
            x += spacing
        y += spacing
    draw_rects(dots, DOT)


def board_draw():
    board = BOARD
    if board is None:
        return
    context = bpy.context
    window = getattr(context, "window", None)
    if window is None or window.as_pointer() != board.window:
        return
    region = getattr(context, "region", None)
    if region is not None:
        board.region = (region.width, region.height)
    else:
        area = getattr(context, "area", None)
        if area is None:
            return
        board.region = (area.width, area.height)
    width, height = board.region

    gpu.state.blend_set("ALPHA")
    draw_rect(0, 0, width, height, BG)
    draw_grid(board, width, height)

    zoom = board.zoom
    for path in board.order:
        x, y, w, h = board.card_rect(path)
        if x + w < 0 or x > width or y + h < 0 or y > height:
            continue
        selected = path in board.selected
        base = CARD_SEL if selected else (CARD_HOT if path == board.hover else CARD)
        draw_rect(x, y, w, h, base)
        pad = 8 * zoom
        image_size = min(w - 2 * pad, h - 64 * zoom)
        ix = x + (w - image_size) / 2
        iy = y + h - pad - image_size
        texture = board.texture(path)
        if texture is not None:
            draw_image(texture, ix, iy, image_size, image_size)
        else:
            draw_rect(ix, iy, image_size, image_size, (0.18, 0.18, 0.19, 1.0))
        if selected:
            thickness = max(2.0, 2.5 * zoom)
            draw_rect(x, y, w, thickness, ACCENT)
            draw_rect(x, y, thickness, h, ACCENT)
            draw_rect(x + w - thickness, y, thickness, h, ACCENT)
            draw_rect(x, y + h - thickness, w, thickness, ACCENT)
            mark = max(10.0, 14 * zoom)
            draw_rect(x + w - mark - 6, y + h - mark - 6, mark, mark, ACCENT)
        elif path == board.hover:
            draw_rect(x, y, w, max(1.0, 1.5 * zoom), DIM)
        if zoom > 0.32:
            draw_text(Path(path).name, x + pad, y + 38 * zoom, 11 * zoom + 3, TEXT)
            entry = next((item for item in board.entries if str(item.path) == path), None)
            if entry is not None and zoom > 0.55:
                draw_text(
                    f"{entry.model_label} · seed {entry.seed}",
                    x + pad,
                    y + 20 * zoom,
                    10 * zoom + 2,
                    DIM,
                )

    draw_rect(0, 0, width, 34, HUD)
    draw_text(
        f"{len(board.order)} images · {len(board.selected)} selected · zoom {board.zoom:.2f} · "
        "wheel zoom · drag empty to pan · drag card to move · shift-click add · "
        "double-click open · right-click actions · F fit · Esc close",
        12,
        11,
        12,
        TEXT,
    )
    gpu.state.blend_set("NONE")


# --------------------------------------------------------------------------
# Selection storage
# --------------------------------------------------------------------------


def selected_paths(context):
    raw = context.scene.get(SELECTION_KEY, "")
    if not raw:
        return []
    return [line for line in str(raw).split("\n") if line]


def store_selection(context, paths):
    context.scene[SELECTION_KEY] = "\n".join(sorted(paths))


# --------------------------------------------------------------------------
# Modal operator
# --------------------------------------------------------------------------


class REMI_BOARD_OT_run(Operator):
    """Own the mouse and draw the board in this window."""

    bl_idname = "remi_board.run"
    bl_label = "Run Board"
    bl_options = {"INTERNAL"}

    def invoke(self, context, _event):
        global BOARD
        if BOARD is not None:
            self.report({"WARNING"}, "A board is already open")
            return {"CANCELLED"}
        window = context.window
        if window is None:
            return {"CANCELLED"}
        board = Board(window)
        board.load(gallery_entries(context))
        board.fit()
        BOARD = board
        area = context.area
        self._area_x = area.x if area is not None else 0
        self._area_y = area.y if area is not None else 0
        board._handle = bpy.types.SpaceView3D.draw_handler_add(
            board_draw, (), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _mouse(self, context, event):
        """Window coordinates are reliable here; region ones are not."""
        window = None
        for candidate in bpy.context.window_manager.windows:
            if BOARD is not None and candidate.as_pointer() == BOARD.window:
                window = candidate
                break
        if window is not None:
            areas = list(window.screen.areas)
            area = next((item for item in areas if item.type == "VIEW_3D"), None)
            if area is None and areas:
                area = max(areas, key=lambda item: item.width * item.height)
            if area is not None:
                self._area_x, self._area_y = area.x, area.y
        return event.mouse_x - self._area_x, event.mouse_y - self._area_y

    def modal(self, context, event):
        board = BOARD
        if board is None:
            return {"CANCELLED"}
        if context.window is None or context.window.as_pointer() != board.window:
            self._finish(context)
            return {"CANCELLED"}
        region = getattr(context, "region", None)
        if region is not None:
            board.region = (region.width, region.height)
        mx, my = self._mouse(context, event)

        if event.type == "ESC" and event.value == "PRESS":
            self._finish(context)
            return {"CANCELLED"}

        if event.type == "MOUSEMOVE":
            if board.card_drag is not None:
                drag = board.card_drag
                nx = (mx - drag["dx"] - board.pan[0]) / board.zoom
                ny = (my - drag["dy"] - board.pan[1]) / board.zoom
                position = board.positions.get(drag["path"])
                if position is not None:
                    board.move_selected(
                        drag["path"], nx - position[0], ny - position[1]
                    )
                if abs(mx - drag["start"][0]) + abs(my - drag["start"][1]) > 3:
                    drag["moved"] = True
            elif board.pan_drag is not None:
                board.pan[0] += mx - board.pan_drag[0]
                board.pan[1] += my - board.pan_drag[1]
                board.pan_drag = (mx, my)
            board.hover = board.hit(mx, my)
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "MIDDLEMOUSE":
            board.pan_drag = (mx, my) if event.value == "PRESS" else None
            return {"RUNNING_MODAL"}

        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"} and event.value == "PRESS":
            factor = 1.15 if event.type == "WHEELUPMOUSE" else 1 / 1.15
            old = board.zoom
            new = max(0.12, min(old * factor, 3.0))
            board.pan[0] = mx - (mx - board.pan[0]) * (new / old)
            board.pan[1] = my - (my - board.pan[1]) * (new / old)
            board.zoom = new
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            path = board.hit(mx, my)
            if path is None:
                if not event.shift:
                    board.selected.clear()
                    store_selection(context, board.selected)
                board.pan_drag = (mx, my)
            else:
                board.raise_card(path)
                if event.shift:
                    board.selected.symmetric_difference_update({path})
                elif path not in board.selected:
                    board.selected = {path}
                store_selection(context, board.selected)
                x, y, _w, _h = board.card_rect(path)
                board.card_drag = {
                    "path": path,
                    "dx": mx - x,
                    "dy": my - y,
                    "moved": False,
                    "start": (mx, my),
                }
                last_path, last_time = board.last_click
                now = time.time()
                board.last_click = (path, now)
                if last_path == path and now - last_time < 0.35:
                    self._open(context, path)
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            board.card_drag = None
            board.pan_drag = None
            return {"RUNNING_MODAL"}

        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            store_selection(context, board.selected)
            bpy.ops.wm.call_menu(name="REMI_BOARD_MT_actions")
            return {"RUNNING_MODAL"}

        if event.type == "A" and event.value == "PRESS":
            board.selected = set(board.order)
            store_selection(context, board.selected)
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "F" and event.value == "PRESS":
            board.fit()
            self._redraw(context)
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    @staticmethod
    def _redraw(context):
        if context.area is not None:
            context.area.tag_redraw()

    @staticmethod
    def _open(context, path):
        opener = getattr(bpy.ops.gen, "open_full", None)
        if opener is None:
            return
        try:
            opener(path=bpy.path.abspath(path))
        except Exception:
            pass

    def _finish(self, context):
        global BOARD
        board = BOARD
        if board is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(board._handle, "WINDOW")
            except Exception:
                pass
            board.cleanup()
            window = None
            for candidate in bpy.context.window_manager.windows:
                if candidate.as_pointer() == board.window:
                    window = candidate
                    break
            BOARD = None
            if window is not None:
                try:
                    with context.temp_override(window=window):
                        bpy.ops.wm.window_close()
                except Exception:
                    pass
        self._redraw(context)

    def cancel(self, context):
        self._finish(context)


# --------------------------------------------------------------------------
# Open / actions / menu / panel
# --------------------------------------------------------------------------


class REMI_BOARD_OT_open(Operator):
    """Open the image board in its own window."""

    bl_idname = "remi_board.open"
    bl_label = "Open Image Board"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if BOARD is not None:
            self.report({"WARNING"}, "A board is already open")
            return {"CANCELLED"}
        before = {window.as_pointer() for window in context.window_manager.windows}
        try:
            bpy.ops.wm.window_new()
        except Exception as exc:
            self.report({"ERROR"}, f"Could not open a window: {exc}")
            return {"CANCELLED"}
        window = next(
            (item for item in context.window_manager.windows if item.as_pointer() not in before),
            None,
        )
        if window is None:
            self.report({"ERROR"}, "The new window was not found")
            return {"CANCELLED"}
        area = max(window.screen.areas, key=lambda item: item.width * item.height)
        if area.type != "VIEW_3D":
            area.type = "VIEW_3D"
        space = area.spaces.active
        if hasattr(space, "show_region_ui"):
            space.show_region_ui = False
        try:
            space.overlay.show_overlays = False
        except Exception:
            pass
        try:
            with context.temp_override(window=window, screen=window.screen, area=area):
                bpy.ops.remi_board.run("INVOKE_DEFAULT")
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start the board: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_action(Operator):
    """Act on the current board selection."""

    bl_idname = "remi_board.action"
    bl_label = "Board Action"
    bl_options = {"REGISTER"}

    action: StringProperty()

    def execute(self, context):
        paths = selected_paths(context)
        if self.action == "select_all":
            if BOARD is not None:
                BOARD.selected = set(BOARD.order)
                store_selection(context, BOARD.selected)
                if context.area:
                    context.area.tag_redraw()
            return {"FINISHED"}
        if self.action == "select_none":
            if BOARD is not None:
                BOARD.selected.clear()
            store_selection(context, set())
            return {"FINISHED"}
        if self.action == "close":
            window = None
            if BOARD is not None:
                for candidate in bpy.context.window_manager.windows:
                    if candidate.as_pointer() == BOARD.window:
                        window = candidate
                        break
            if window is not None:
                with context.temp_override(window=window):
                    bpy.ops.wm.window_close()
            return {"FINISHED"}
        if not paths:
            self.report({"WARNING"}, "Nothing selected on the board")
            return {"CANCELLED"}
        if self.action == "open":
            opener = getattr(bpy.ops.gen, "open_full", None)
            if opener is None:
                self.report({"WARNING"}, "Load ui_generate_sketch.py for the viewer")
                return {"CANCELLED"}
            opener(path=bpy.path.abspath(paths[0]))
            return {"FINISHED"}
        if self.action == "splats":
            if not bpy.ops.remi.generate_splats.poll():
                self.report({"WARNING"}, "Another job is running")
                return {"CANCELLED"}
            bpy.ops.remi.generate_splats("INVOKE_DEFAULT", image_path=paths[0])
            return {"FINISHED"}
        return {"CANCELLED"}


class REMI_BOARD_MT_actions(Menu):
    bl_idname = "REMI_BOARD_MT_actions"
    bl_label = "Board"

    def draw(self, context):
        layout = self.layout
        paths = selected_paths(context)
        layout.label(text=f"{len(paths)} selected" if paths else "Nothing selected")
        splats = layout.operator("remi_board.action", text="Generate 3D from selection", icon="OUTLINER_OB_POINTCLOUD")
        splats.action = "splats"
        open_image = layout.operator("remi_board.action", text="Open Full Size", icon="ZOOM_IN")
        open_image.action = "open"
        layout.separator()
        select_all = layout.operator("remi_board.action", text="Select All", icon="SELECT_SET")
        select_all.action = "select_all"
        select_none = layout.operator("remi_board.action", text="Select None", icon="SELECT_EXTEND")
        select_none.action = "select_none"
        layout.separator()
        close = layout.operator("remi_board.action", text="Close Board", icon="CANCEL")
        close.action = "close"


class REMI_BOARD_PT_panel(Panel):
    bl_label = "Image board"
    bl_idname = "REMI_BOARD_PT_panel"
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
            layout.label(text="Remi did not load", icon="ERROR")
        layout.operator("remi_board.open", text="Open Image Board", icon="WINDOW")


CLASSES = (
    REMI_BOARD_OT_run,
    REMI_BOARD_OT_open,
    REMI_BOARD_OT_action,
    REMI_BOARD_MT_actions,
    REMI_BOARD_PT_panel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global BOARD
    if BOARD is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(BOARD._handle, "WINDOW")
        except Exception:
            pass
        BOARD.cleanup()
        BOARD = None
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def main():
    register()
    boot()
    print(f"Remi board sketch ready: N-panel → GEN → Open Image Board. Remi source: {BOOT['source']}")


if __name__ == "__main__":
    main()
