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
import math
import os
import sys
import time
from pathlib import Path

import blf
import bpy
import gpu
from bpy.types import Operator, Panel
from gpu_extras.batch import batch_for_shader

BOOT = {"source": "not loaded", "error": ""}
SELECTION_KEY = "remi_board_selection"

CELL_W = 300.0
CELL_H = 300.0
GAP = 30.0
COLUMNS = 4
THUMB = 512

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
        self.selected = set()
        self.textures = {}
        self.datablocks = []
        self.region = (1440, 810)
        self.pan_drag = None
        self.card_drag = None
        self.last_click = (None, 0.0)
        self.composer = ""
        self.caret = 0
        self.focused = False
        self.queue = []
        self.running = ""
        self.known = 0

    # data ---------------------------------------------------------------
    def load(self, entries):
        self.entries = entries
        self.known = len(entries)
        self.order = [str(entry.path) for entry in entries]
        for index, entry in enumerate(entries):
            self.positions[str(entry.path)] = self.grid_position(index)

    @staticmethod
    def grid_position(index):
        column = index % COLUMNS
        row = index // COLUMNS
        return [column * (CELL_W + GAP), -(row * (CELL_H + GAP))]

    def reload(self, context):
        entries = gallery_entries(context)
        known = {str(entry.path) for entry in entries}
        self.entries = entries
        placed = len(self.positions)
        for entry in entries:
            path = str(entry.path)
            if path not in self.positions:
                self.positions[path] = self.grid_position(placed)
                placed += 1
            if path not in self.order:
                self.order.append(path)
        for path in list(self.order):
            if path not in known:
                self.order.remove(path)
                self.positions.pop(path, None)
                self.selected.discard(path)
        self.known = len(entries)

    # geometry -----------------------------------------------------------
    def cell(self, index):
        column = index % COLUMNS
        row = index // COLUMNS
        bx = column * (CELL_W + GAP)
        by = -(row * (CELL_H + GAP))
        return bx * self.zoom + self.pan[0], by * self.zoom + self.pan[1]

    def card_rect(self, path):
        index = self.order.index(path) if path in self.order else 0
        x, y = self.cell(index)
        return x, y, CELL_W * self.zoom, CELL_H * self.zoom

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
        left, right = min(xs), max(xs) + CELL_W
        bottom, top = min(ys), max(ys) + CELL_H
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
    bar_w = max(360.0, min(560.0, width - 120.0))
    bar_h = 58.0
    x = (width - bar_w) / 2
    y = 22.0
    draw_rect(x, y, bar_w, bar_h, (0.105, 0.105, 0.115, 0.97))
    if board.focused:
        draw_rect(x, y, bar_w, 1.5, ACCENT)

    field_x = x + 14
    field_w = bar_w - 140
    text = board.composer
    size = 13
    if text:
        shown = clip_text(text, size, field_w)
        if board.focused:
            offset = max(0.0, text_width(text[: board.caret], size) - field_w + 12)
            draw_text(
                clip_text(text, size, field_w),
                field_x - offset,
                y + 22,
                size,
                TEXT,
            )
        else:
            draw_text(shown, field_x, y + 22, size, TEXT)
    else:
        draw_text("Describe an image…", field_x, y + 22, size, DIM)
    if board.focused and (time.time() % 1.0) < 0.55:
        offset = 0.0
        if text:
            offset = max(0.0, text_width(text[: board.caret], size) - field_w + 12)
        caret = text_width(text[: board.caret], size) - offset
        draw_rect(field_x + caret + 1, y + 19, 1.4, 17, TEXT)

    button_w = 112.0
    button_x = x + bar_w - button_w - 12
    state = bpy.context.window_manager.remi_image
    busy = state.busy or bool(board.queue)
    draw_rect(button_x, y + 12, button_w, 34, ACCENT if not busy else (0.24, 0.24, 0.25, 1.0))
    label = f"{int(state.progress * 100)}%" if state.busy else ("Queued" if busy else "Create")
    draw_text(
        label,
        button_x + 24,
        y + 23,
        12,
        (0.06, 0.06, 0.07, 1.0) if not busy else TEXT,
    )


def placeholder_cards(board, width, height):
    """Empty cards for jobs that are queued or running."""
    slots = list(board.queue)
    if board.running:
        slots.insert(0, board.running)
    for offset, prompt in enumerate(slots):
        index = len(board.order) + offset
        x, y = board.cell(index)
        w, h = CELL_W * board.zoom, CELL_H * board.zoom
        if x + w < 0 or x > width or y + h < 0 or y > height:
            continue
        draw_rect(x, y, w, h, EMPTY)
        label = "generating…" if offset == 0 and board.running else "queued"
        draw_text(label, x + 12, y + h - 24, 12, DIM)
        if offset == 0 and board.running:
            state = bpy.context.window_manager.remi_image
            draw_rect(x + 12, y + 10, max(2.0, (w - 24) * state.progress), 3.0, ACCENT)


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
        return
    try:
        result = bpy.ops.remi.generate_image("INVOKE_DEFAULT")
    except Exception:
        board.queue.clear()
        return
    if {"FINISHED", "RUNNING_MODAL"} & set(result):
        board.running = prompt
    else:
        board.queue.clear()


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
        board.load(gallery_entries(context))
        board.composer = context.scene.remi_image.prompt or ""
        board.caret = len(board.composer)
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

    # composer helpers ---------------------------------------------------
    def _field_rect(self, board):
        width = board.region[0]
        bar_w = max(360.0, min(560.0, width - 120.0))
        x = (width - bar_w) / 2
        return x + 14, 22.0 + 10, bar_w - 140, 38

    def _caret_from_x(self, board, mx, field_x):
        size = 13
        target = mx - field_x
        best = 0
        for index in range(len(board.composer) + 1):
            width = text_width(board.composer[:index], size)
            if width >= target:
                return index
            best = index
        return best

    def _submit(self, context):
        board = BOARD
        if board is None:
            return
        text = board.composer.strip()
        if not text:
            return
        board.queue.append(text)
        board.composer = ""
        board.caret = 0
        start_next(context)
        self._redraw(context)

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
            if board.focused:
                board.focused = False
                self._redraw(context)
                return {"RUNNING_MODAL"}
            self._finish(context)
            return {"CANCELLED"}

        if board.focused and event.value == "PRESS":
            text = board.composer
            if event.type == "BACK_SPACE":
                if board.caret > 0:
                    board.composer = text[: board.caret - 1] + text[board.caret :]
                    board.caret -= 1
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if event.type == "DELETE":
                if board.caret < len(text):
                    board.composer = text[: board.caret] + text[board.caret + 1 :]
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if event.type in {"LEFT_ARROW", "RIGHT_ARROW", "HOME", "END"}:
                if event.type == "LEFT_ARROW":
                    board.caret = max(0, board.caret - 1)
                elif event.type == "RIGHT_ARROW":
                    board.caret = min(len(text), board.caret + 1)
                elif event.type == "HOME":
                    board.caret = 0
                else:
                    board.caret = len(text)
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if event.type == "RETURN":
                self._submit(context)
                return {"RUNNING_MODAL"}
            char = getattr(event, "unicode", "")
            if char and char.isprintable() and not event.ctrl and not event.alt:
                board.composer = text[: board.caret] + char + text[board.caret :]
                board.caret += len(char)
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if event.type not in {
                "MOUSEMOVE",
                "LEFTMOUSE",
                "MIDDLEMOUSE",
                "WHEELUPMOUSE",
                "WHEELDOWNMOUSE",
            }:
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            if board.card_drag is not None:
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
            field_x, field_y, field_w, field_h = self._field_rect(board)
            width = board.region[0]
            bar_w = max(360.0, min(560.0, width - 120.0))
            bar_x = (width - bar_w) / 2
            button = (bar_x + bar_w - 124, 34, 112, 34)
            if field_x <= mx <= field_x + field_w and field_y <= my <= field_y + field_h:
                board.focused = True
                board.caret = self._caret_from_x(board, mx, field_x)
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if button[0] <= mx <= button[0] + button[2] and button[1] <= my <= button[1] + button[3]:
                board.focused = False
                self._submit(context)
                return {"RUNNING_MODAL"}
            board.focused = False
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

    def _finish(self, context):
        global BOARD
        board = BOARD
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


def board_tick():
    """Advance the queue and pick up finished images."""
    board = BOARD
    if board is None:
        return None
    context = bpy.context
    state = context.window_manager.remi_image
    if not state.busy:
        entries = gallery_entries(context)
        if len(entries) > board.known:
            board.reload(context)
        board.running = ""
        start_next(context)
    for window in context.window_manager.windows:
        if window.as_pointer() == board.window:
            for area in window.screen.areas:
                area.tag_redraw()
    return 0.5


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
                bpy.ops.remi_board.run("INVOKE_DEFAULT")
        except Exception as exc:
            self.report({"ERROR"}, f"Could not start the board: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


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


CLASSES = (REMI_BOARD_OT_run, REMI_BOARD_OT_open, REMI_BOARD_PT_panel)


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
