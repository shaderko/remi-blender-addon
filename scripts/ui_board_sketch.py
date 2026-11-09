"""Image board — GPU canvas in its own window, no scene content.

    scripts\\run-ui-sketch.bat

Deliberately minimal:

- canvas: images only, each scaled to its own aspect ratio, no labels or frames
- wheel zooms around the cursor, drag moves a card or pans empty space
- click / shift-click selects; the selection is an accent outline
- double-click opens the image in its own window
- composer: one compact centred bar; type a prompt, Enter or Create queues it
- a queued job shows an empty card with a progress line until its image lands

Images are datablocks for texturing only; nothing enters the scene and they are
removed when the board closes.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
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
LAYOUT_KEY = "remi_board_layout"

CELL_W = 300.0
CELL_H = 300.0
GAP = 30.0
COLUMNS = 4
THUMB = 512
RESIZE_PX = 16.0
MIN_SCALE = 0.25
MAX_SCALE = 4.0

BG = (0.152, 0.152, 0.158, 1.0)
DOT = (0.235, 0.235, 0.245, 1.0)
TEXT = (0.88, 0.88, 0.92, 1.0)
DIM = (0.55, 0.55, 0.6, 1.0)
ACCENT = (1.0, 0.55, 0.15, 1.0)
EMPTY = (0.11, 0.11, 0.12, 1.0)

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
# GPU helpers
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


def text_width(text, size):
    if not text:
        return 0.0
    blf.size(0, int(max(8, size)))
    return blf.dimensions(0, text)[0]


def clip_text(text, size, limit):
    if text_width(text, size) <= limit:
        return text
    while text and text_width(text + "…", size) > limit:
        text = text[:-1]
    return text + "…"


def open_image_window(context, path):
    try:
        image = bpy.data.images.load(bpy.path.abspath(path), check_existing=True)
    except Exception:
        return False
    manager = context.window_manager
    before = {window.as_pointer() for window in manager.windows}
    try:
        bpy.ops.wm.window_new()
    except Exception:
        return False
    window = next((item for item in manager.windows if item.as_pointer() not in before), None)
    if window is None:
        return False
    areas = list(window.screen.areas)
    area = max(areas, key=lambda item: item.width * item.height) if areas else None
    if area is None:
        return False
    area.type = "IMAGE_EDITOR"
    for space in area.spaces:
        if space.type == "IMAGE_EDITOR":
            space.image = image
            space.show_region_ui = False
            space.zoom = 1.0
            break
    return True


def cycle_enum(owner, name):
    try:
        items = list(owner.bl_rna.properties[name].enum_items)
    except Exception:
        return
    identifiers = [item.identifier for item in items]
    current = getattr(owner, name)
    index = identifiers.index(current) if current in identifiers else -1
    setattr(owner, name, identifiers[(index + 1) % len(identifiers)])


# --------------------------------------------------------------------------
# Board
# --------------------------------------------------------------------------


class Board:
    def __init__(self, window):
        self.window = window.as_pointer()
        self.zoom = 0.8
        self.pan = [120.0, 620.0]
        self.entries = []
        self.order = []
        self.positions = {}
        self.scales = {}
        self.selected = set()
        self.textures = {}
        self.datablocks = []
        self.region = (1440, 810)
        self.pan_drag = None
        self.card_drag = None
        self.resize_drag = None
        self.last_click = (None, 0.0)
        self.queue = []
        self.running = ""
        self.pending = []
        self.counter = 0
        self.ticks = 0
        self.known = 0

    # data ---------------------------------------------------------------
    def load(self, context, entries):
        layout = load_layout(context)
        self.entries = entries
        self.known = len(entries)
        self.order = [str(entry.path) for entry in entries]
        self.positions = {}
        saved = layout.get("positions") if isinstance(layout, dict) else None
        paths = {str(entry.path) for entry in entries}
        if isinstance(saved, dict):
            for path, position in saved.items():
                if str(path) not in paths:
                    continue
                if isinstance(position, (list, tuple)) and len(position) == 2:
                    self.positions[str(path)] = [float(position[0]), float(position[1])]
            self.zoom = float(layout.get("zoom", self.zoom))
            pan = layout.get("pan")
            if isinstance(pan, (list, tuple)) and len(pan) == 2:
                self.pan = [float(pan[0]), float(pan[1])]
            saved_scales = layout.get("scales")
            if isinstance(saved_scales, dict):
                for path, value in saved_scales.items():
                    try:
                        self.scales[str(path)] = max(MIN_SCALE, min(MAX_SCALE, float(value)))
                    except (TypeError, ValueError):
                        continue
            for index, entry in enumerate(entries):
                path = str(entry.path)
                if path not in self.positions:
                    self.positions[path] = self.new_slot()
        else:
            for index, entry in enumerate(entries):
                self.positions[str(entry.path)] = self.grid_position(index)

    @staticmethod
    def grid_position(index):
        column = index % COLUMNS
        row = index // COLUMNS
        return [column * (CELL_W + GAP), -(row * (CELL_H + GAP))]

    def new_slot(self):
        if not self.positions:
            return [0.0, 0.0]
        left = min(position[0] for position in self.positions.values())
        bottom = min(position[1] for position in self.positions.values())
        return [left, bottom - CELL_H - GAP]

    def add_pending(self, prompt):
        key = f"pending:{self.counter}"
        self.counter += 1
        self.pending.append({"key": key, "prompt": prompt})
        self.positions[key] = self.new_slot()
        self.order.append(key)
        return key

    def drop_oldest_pending(self):
        if not self.pending:
            return None
        slot = self.pending.pop(0)
        key = slot["key"]
        if key in self.order:
            self.order.remove(key)
        self.positions.pop(key, None)
        self.scales.pop(key, None)
        self.selected.discard(key)
        return key

    def forget(self, path):
        if path in self.order:
            self.order.remove(path)
        self.positions.pop(path, None)
        self.scales.pop(path, None)
        self.selected.discard(path)

    def reload(self, context):
        entries = gallery_entries(context)
        known = {str(entry.path) for entry in entries}
        self.entries = entries
        for entry in entries:
            path = str(entry.path)
            if path in self.positions:
                if path not in self.order:
                    self.order.append(path)
                continue
            slot = self.pending.pop(0) if self.pending else None
            if slot is not None:
                key = slot["key"]
                self.positions[path] = self.positions.pop(key, self.new_slot())
                if key in self.scales:
                    self.scales[path] = self.scales.pop(key)
                if key in self.order:
                    self.order[self.order.index(key)] = path
                else:
                    self.order.append(path)
                if key in self.selected:
                    self.selected.discard(key)
                    self.selected.add(path)
            else:
                self.positions[path] = self.new_slot()
                self.order.append(path)
        for path in list(self.order):
            if path.startswith("pending:"):
                continue
            if path not in known:
                self.order.remove(path)
                self.positions.pop(path, None)
                self.scales.pop(path, None)
                self.selected.discard(path)
        self.known = len(entries)
        save_layout(context)

    # geometry -----------------------------------------------------------
    def scale_of(self, path):
        return self.scales.get(path, 1.0)

    def card_rect(self, path):
        x, y = self.positions.get(path, (0.0, 0.0))
        scale = self.scale_of(path)
        return (
            x * self.zoom + self.pan[0],
            y * self.zoom + self.pan[1],
            CELL_W * scale * self.zoom,
            CELL_H * scale * self.zoom,
        )

    def corner_handle(self, path):
        x, y, w, _h = self.card_rect(path)
        return x + w, y

    def resize_target(self, mx, my):
        for path in reversed(self.order):
            hx, hy = self.corner_handle(path)
            if abs(mx - hx) <= RESIZE_PX and abs(my - hy) <= RESIZE_PX:
                return path
        return None

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
        xs = [self.positions[p][0] for p in self.order if p in self.positions]
        ys = [self.positions[p][1] for p in self.order if p in self.positions]
        if not xs:
            return
        right = max(self.positions[p][0] + CELL_W * self.scale_of(p) for p in self.order if p in self.positions)
        top = max(self.positions[p][1] + CELL_H * self.scale_of(p) for p in self.order if p in self.positions)
        left, bottom = min(xs), min(ys)
        width, height = self.region
        board_w, board_h = max(1.0, right - left), max(1.0, top - bottom)
        self.zoom = max(0.12, min(width / (board_w + 100), height / (board_h + 200), 1.5))
        self.pan[0] = (width - board_w * self.zoom) / 2 - left * self.zoom
        self.pan[1] = (height - board_h * self.zoom) / 2 - bottom * self.zoom + 40

    # textures -----------------------------------------------------------
    def texture(self, path):
        if path in self.textures:
            return self.textures[path]
        try:
            image = bpy.data.images.load(path, check_existing=False)
            width, height = image.size
            if max(width, height) > THUMB:
                scale = THUMB / max(width, height)
                image.scale(max(1, int(width * scale)), max(1, int(height * scale)))
                width, height = image.size
            texture = gpu.texture.from_image(image)
        except Exception:
            self.textures[path] = None
            return None
        self.datablocks.append(image)
        entry = (texture, max(1, width), max(1, height))
        self.textures[path] = entry
        return entry

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
    spacing = 28.0 * board.zoom
    while spacing < 16.0:
        spacing *= 2.0
    while spacing > 80.0:
        spacing /= 2.0
    start_x = board.pan[0] % spacing
    start_y = board.pan[1] % spacing
    dots = []
    y = start_y
    while y < height:
        x = start_x
        while x < width:
            dots.append((x, y, 1.8, 1.8))
            x += spacing
        y += spacing
    draw_rects(dots, DOT)


def draw_composer(board, width):
    bar_w = max(380.0, min(520.0, width - 120.0))
    bar_h = 46.0
    x = (width - bar_w) / 2
    y = 24.0
    draw_rect(x, y, bar_w, bar_h, (0.105, 0.105, 0.115, 0.97))

    field_x = x + 10
    field_w = bar_w - 146
    draw_rect(field_x, y + 8, field_w, 30, (0.075, 0.075, 0.085, 1.0))
    prompt = bpy.context.scene.remi_image.prompt or ""
    label = clip_text(prompt, 13, field_w - 18) if prompt else "Describe an image…"
    draw_text(label, field_x + 9, y + 19, 13, TEXT if prompt else DIM)

    button_w = 118.0
    button_x = x + bar_w - button_w - 12
    state = bpy.context.window_manager.remi_image
    busy = state.busy or bool(board.queue)
    draw_rect(
        button_x,
        y + 8,
        button_w,
        30,
        (0.24, 0.24, 0.25, 1.0) if busy else ACCENT,
    )
    draw_text(
        "Create",
        button_x + 34,
        y + 19,
        12,
        TEXT if busy else (0.06, 0.06, 0.07, 1.0),
    )


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
    pulse = 0.5 + 0.5 * math.sin(time.time() * 2.6)
    for path in board.order:
        x, y, w, h = board.card_rect(path)
        if x + w < 0 or x > width or y + h < 0 or y > height:
            continue
        if path.startswith("pending:"):
            grey = 0.13 + 0.055 * pulse
            draw_rect(x, y, w, h, (grey, grey, grey + 0.008, 1.0))
            if path in board.selected:
                thickness = max(1.5, 2.0 * zoom)
                draw_rect(x, y, w, thickness, ACCENT)
                draw_rect(x, y, thickness, h, ACCENT)
                draw_rect(x + w - thickness, y, thickness, h, ACCENT)
                draw_rect(x, y + h - thickness, w, thickness, ACCENT)
            if path == board.hover or path in board.selected:
                handle = max(7.0, 9.0 * zoom)
                draw_rect(x + w - handle / 2, y - handle / 2, handle, handle, ACCENT)
            continue
        cached = board.texture(path)
        if cached is None:
            continue
        texture, image_w, image_h = cached
        aspect = image_w / image_h
        if aspect >= 1.0:
            draw_w = w
            draw_h = w / aspect
        else:
            draw_h = h
            draw_w = h * aspect
        draw_x = x + (w - draw_w) / 2
        draw_y = y + (h - draw_h) / 2
        draw_image(texture, draw_x, draw_y, draw_w, draw_h)
        if path in board.selected:
            thickness = max(1.5, 2.0 * zoom)
            draw_rect(draw_x, draw_y, draw_w, thickness, ACCENT)
            draw_rect(draw_x, draw_y, thickness, draw_h, ACCENT)
            draw_rect(draw_x + draw_w - thickness, draw_y, thickness, draw_h, ACCENT)
            draw_rect(draw_x, draw_y + draw_h - thickness, draw_w, thickness, ACCENT)
        if path == board.hover or path in board.selected:
            handle = max(7.0, 9.0 * zoom)
            hx, hy = draw_x + draw_w, draw_y
            draw_rect(hx - handle / 2, hy - handle / 2, handle, handle, ACCENT)

    placeholder_cards(board, width, height)
    draw_composer(board, width)
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


def load_layout(context):
    raw = context.scene.get(LAYOUT_KEY, "")
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_layout(context):
    board = BOARD
    if board is None:
        return
    payload = {
        "positions": {
            str(path): [round(float(pos[0]), 2), round(float(pos[1]), 2)]
            for path, pos in board.positions.items()
            if not str(path).startswith("pending:")
        },
        "scales": {
            str(path): round(float(value), 4)
            for path, value in board.scales.items()
            if not str(path).startswith("pending:")
        },
        "zoom": round(float(board.zoom), 4),
        "pan": [round(float(board.pan[0]), 2), round(float(board.pan[1]), 2)],
    }
    try:
        context.scene[LAYOUT_KEY] = json.dumps(payload)
    except Exception:
        pass


def start_next(context):
    """Start the next queued prompt if nothing is running."""
    board = BOARD
    if board is None or board.running or not board.queue:
        return
    state = context.window_manager.remi_image
    if state.busy:
        return
    prompt = board.queue.pop(0)
    context.scene.remi_image.prompt = prompt
    if not bpy.ops.remi.generate_image.poll():
        board.queue.clear()
        while board.pending:
            board.drop_oldest_pending()
        return
    try:
        result = bpy.ops.remi.generate_image("INVOKE_DEFAULT")
    except Exception:
        board.queue.clear()
        while board.pending:
            board.drop_oldest_pending()
        return
    if {"FINISHED", "RUNNING_MODAL"} & set(result):
        board.running = prompt
    else:
        board.queue.clear()
        while board.pending:
            board.drop_oldest_pending()


# --------------------------------------------------------------------------
# Modal
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
        board.load(context, gallery_entries(context))
        board.fit()
        BOARD = board
        area = context.area
        self._area_x = area.x if area is not None else 0
        self._area_y = area.y if area is not None else 0
        board._handle = bpy.types.SpaceView3D.draw_handler_add(
            board_draw, (), "WINDOW", "POST_PIXEL"
        )
        if not bpy.app.timers.is_registered(board_tick):
            bpy.app.timers.register(board_tick, first_interval=0.5)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _mouse(self, context, event):
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
            if board.resize_drag is not None:
                drag = board.resize_drag
                delta = (mx - drag["start"][0]) / max(1.0, CELL_W * board.zoom)
                board.scales[drag["path"]] = max(
                    MIN_SCALE, min(MAX_SCALE, drag["scale"] + delta)
                )
            elif board.card_drag is not None:
                drag = board.card_drag
                nx = (mx - drag["dx"] - board.pan[0]) / board.zoom
                ny = (my - drag["dy"] - board.pan[1]) / board.zoom
                position = board.positions.get(drag["path"])
                if position is not None:
                    board.move_selected(drag["path"], nx - position[0], ny - position[1])
            elif board.pan_drag is not None:
                board.pan[0] += mx - board.pan_drag[0]
                board.pan[1] += my - board.pan_drag[1]
                board.pan_drag = (mx, my)
            else:
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

        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            path = board.hit(mx, my)
            if path is not None and not path.startswith("pending:"):
                board.selected = {path}
                store_selection(context, board.selected)
                context.scene["remi_board_target"] = path
                try:
                    bpy.ops.wm.call_menu(name="REMI_BOARD_MT_image")
                except Exception:
                    pass
                self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            width = board.region[0]
            bar_w = max(380.0, min(520.0, width - 120.0))
            bar_x = (width - bar_w) / 2
            field_rect = (bar_x + 10, 32, bar_w - 146, 30)
            button_rect = (bar_x + bar_w - 130, 32, 118, 30)
            if (
                field_rect[0] <= mx <= field_rect[0] + field_rect[2]
                and field_rect[1] <= my <= field_rect[1] + field_rect[3]
            ):
                try:
                    bpy.ops.wm.call_panel(name="REMI_BOARD_PT_composer", keep_open=True)
                except Exception:
                    pass
                return {"RUNNING_MODAL"}
            if (
                button_rect[0] <= mx <= button_rect[0] + button_rect[2]
                and button_rect[1] <= my <= button_rect[1] + button_rect[3]
            ):
                submit_current(context)
                self._redraw(context)
                return {"RUNNING_MODAL"}
            corner = board.resize_target(mx, my)
            if corner is not None:
                board.raise_card(corner)
                board.selected = {corner}
                store_selection(context, board.selected)
                board.resize_drag = {
                    "path": corner,
                    "start": (mx, my),
                    "scale": board.scale_of(corner),
                }
                self._redraw(context)
                return {"RUNNING_MODAL"}
            path = board.hit(mx, my)
            if path is None:
                if not event.shift:
                    board.selected.clear()
                    store_selection(context, board.selected)
                board.pan_drag = (mx, my)
            else:
                board.raise_card(path)
                if path.startswith("pending:"):
                    x, y, _w, _h = board.card_rect(path)
                    board.card_drag = {"path": path, "dx": mx - x, "dy": my - y}
                    self._redraw(context)
                    return {"RUNNING_MODAL"}
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
                }
                last_path, last_time = board.last_click
                now = time.time()
                board.last_click = (path, now)
                if last_path == path and now - last_time < 0.35:
                    open_image_window(context, path)
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            board.card_drag = None
            board.pan_drag = None
            board.resize_drag = None
            save_layout(context)
            return {"RUNNING_MODAL"}

        if event.type == "A" and event.value == "PRESS":
            board.selected = set(board.order)
            store_selection(context, board.selected)
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "F" and event.value == "PRESS":
            board.fit()
            save_layout(context)
            self._redraw(context)
            return {"RUNNING_MODAL"}

        return {"RUNNING_MODAL"}

    @staticmethod
    def _redraw(context):
        if context.area is not None:
            context.area.tag_redraw()

    def _finish(self, context):
        global BOARD
        board = BOARD
        save_layout(context)
        if bpy.app.timers.is_registered(board_tick):
            bpy.app.timers.unregister(board_tick)
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


def submit_current(context):
    """Queue the prompt currently in the scene's native text field."""
    board = BOARD
    if board is None:
        return False
    text = context.scene.remi_image.prompt.strip()
    if not text:
        return False
    board.add_pending(text)
    board.queue.append(text)
    save_layout(context)
    start_next(context)
    return True


def deletion_target(path):
    """The file or generation folder to remove for this image, inside the output dir."""
    context = bpy.context
    try:
        output = Path(bpy.path.abspath(context.scene.remi_image.output)).resolve()
    except Exception:
        return None
    file = Path(bpy.path.abspath(path))
    try:
        file = file.resolve()
    except OSError:
        return None
    parent = file.parent
    if parent == output:
        return file
    if output in parent.parents:
        return parent
    return None


def delete_generation(path):
    """Delete the image's generation from disk. Returns what was removed."""
    target = deletion_target(path)
    if target is None:
        return None
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
    except OSError:
        return None
    return target


def board_tick():
    """Pulse pending cards, advance the queue, pick up finished images."""
    board = BOARD
    if board is None:
        return None
    context = bpy.context
    state = context.window_manager.remi_image
    board.ticks += 1
    if not state.busy and board.ticks % 3 == 0:
        entries = gallery_entries(context)
        if len(entries) > board.known:
            board.reload(context)
            board.running = ""
        elif board.running:
            board.drop_oldest_pending()
            board.running = ""
        start_next(context)
    for window in context.window_manager.windows:
        if window.as_pointer() == board.window:
            for area in window.screen.areas:
                area.tag_redraw()
    return 0.15


# --------------------------------------------------------------------------
# Open operator / panel
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
                bpy.ops.screen.header()
        except Exception:
            try:
                with context.temp_override(window=window, screen=window.screen, area=area):
                    bpy.ops.screen.region_toggle(region="HEADER")
            except Exception:
                pass
        try:
            with context.temp_override(window=window, screen=window.screen, area=area):
                bpy.ops.remi_board.run("INVOKE_DEFAULT")
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start the board: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_delete(Operator):
    """Delete this image's generation from disk."""

    bl_idname = "remi_board.delete"
    bl_label = "Delete Image"
    bl_description = "Remove this generation's folder from disk, including every image and its metadata"
    bl_options = {"REGISTER", "UNDO"}

    path: StringProperty()

    def invoke(self, context, event):
        target = deletion_target(self.path)
        if target is None:
            self.report({"ERROR"}, "That file is not inside the image output folder")
            return {"CANCELLED"}
        subject = "folder and everything in it" if target.is_dir() else "file"
        return context.window_manager.invoke_confirm(
            self,
            event,
            title="Delete generation?",
            message=f"Removes the {subject} '{target.name}'. There will be no local copy left.",
            confirm_text="Delete",
            icon="WARNING",
        )

    def execute(self, context):
        target = delete_generation(self.path)
        if target is None:
            self.report({"ERROR"}, "Could not delete that generation")
            return {"CANCELLED"}
        gallery_module = sys.modules.get("remi.features.images.gallery")
        if gallery_module is not None:
            try:
                gallery_module.gallery.invalidate()
            except Exception:
                pass
        board = BOARD
        if board is not None:
            board.forget(self.path)
            board.reload(context)
            save_layout(context)
            for window in context.window_manager.windows:
                if window.as_pointer() == board.window:
                    for area in window.screen.areas:
                        area.tag_redraw()
        self.report({"INFO"}, f"Deleted {target.name}")
        return {"FINISHED"}


class REMI_BOARD_MT_image(Menu):
    bl_idname = "REMI_BOARD_MT_image"
    bl_label = "Image"

    def draw(self, context):
        layout = self.layout
        path = str(context.scene.get("remi_board_target", ""))
        if not path:
            layout.label(text="No image", icon="INFO")
            return
        layout.operator("remi_board.delete", text="Delete", icon="TRASH").path = path


class REMI_BOARD_OT_create(Operator):
    """Queue the prompt from the native field."""

    bl_idname = "remi_board.create"
    bl_label = "Create Image"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if not submit_current(context):
            self.report({"WARNING"}, "Type a prompt first")
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_PT_composer(Panel):
    """The native prompt field, opened from the board's bottom bar."""

    bl_label = "Prompt"
    bl_idname = "REMI_BOARD_PT_composer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 34

    def draw(self, context):
        layout = self.layout
        settings = context.scene.remi_image
        layout.use_property_split = False
        layout.prop(settings, "prompt", text="")
        row = layout.row(align=True)
        row.prop(settings, "model", text="")
        row.prop(settings, "size", text="")
        row = layout.row(align=True)
        row.prop(settings, "count", text="Images")
        row.prop(settings, "steps", text="Steps")
        state = context.window_manager.remi_image
        if state.busy:
            layout.progress(
                factor=float(state.progress),
                type="BAR",
                text=f"{int(state.progress * 100)}%",
            )
        else:
            column = layout.column()
            column.scale_y = 1.4
            column.operator("remi_board.create", text="Create image", icon="PLAY")


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
    REMI_BOARD_OT_create,
    REMI_BOARD_OT_delete,
    REMI_BOARD_MT_image,
    REMI_BOARD_PT_composer,
    REMI_BOARD_PT_panel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global BOARD
    if bpy.app.timers.is_registered(board_tick):
        bpy.app.timers.unregister(board_tick)
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
