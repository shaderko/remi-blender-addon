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

CELL_W = 300.0
CELL_H = 300.0
GAP = 30.0
COLUMNS = 4
THUMB = 512
RESIZE_PX = 16.0
MIN_SCALE = 0.25
MAX_SCALE = 4.0

TEXT_SIZE = 13.0
LINE_H = 18.0
MIN_LINES = 1
MAX_LINES = 7
BAR_PAD = 12.0
TEXT_PAD = 10.0
BUTTON_W = 112.0
BUTTON_H = 28.0

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
    try:
        blf.size(0, int(max(8, size)))
        return blf.dimensions(0, text)[0]
    except Exception:
        return len(text) * size * 0.55


def clip_text(text, size, limit):
    if text_width(text, size) <= limit:
        return text
    while text and text_width(text + "…", size) > limit:
        text = text[:-1]
    return text + "…"


def wrap_lines(text, size, width):
    """Visual lines as (start, end) index pairs, wrapping on spaces."""
    lines = []
    line_start = 0
    last_space = -1
    used = 0.0
    index = 0
    total = len(text)
    while index < total:
        char = text[index]
        if char == "\n":
            lines.append((line_start, index))
            index += 1
            line_start = index
            last_space = -1
            used = 0.0
            continue
        advance = text_width(char, size)
        if used + advance > width and index > line_start:
            if last_space >= line_start:
                lines.append((line_start, last_space))
                line_start = last_space + 1
                index = line_start
            else:
                lines.append((line_start, index))
                line_start = index
            last_space = -1
            used = 0.0
            continue
        if char == " ":
            last_space = index
        used += advance
        index += 1
    lines.append((line_start, total))
    return lines


def caret_line(lines, caret):
    for number, (start, end) in enumerate(lines):
        if start <= caret <= end:
            return number, caret - start
    last = len(lines) - 1
    start, end = lines[last]
    return last, caret - start


def caret_from_point(lines, text, x, y, size, text_x, text_top):
    line_index = int(max(0.0, (text_top - y) // LINE_H))
    line_index = min(line_index, len(lines) - 1)
    start, end = lines[line_index]
    best = start
    best_distance = None
    for offset in range(end - start + 1):
        width = text_width(text[start : start + offset], size)
        distance = abs(width - x)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = start + offset
    return best


def _word_char(char):
    return char.isalnum() or char == "_"


def word_left(text, index):
    while index > 0 and text[index - 1].isspace():
        index -= 1
    if index > 0 and not _word_char(text[index - 1]):
        while index > 0 and not text[index - 1].isspace() and not _word_char(text[index - 1]):
            index -= 1
        return index
    while index > 0 and _word_char(text[index - 1]):
        index -= 1
    return index


def word_right(text, index):
    total = len(text)
    while index < total and text[index].isspace():
        index += 1
    if index < total and not _word_char(text[index]):
        while index < total and not text[index].isspace() and not _word_char(text[index]):
            index += 1
        return index
    while index < total and _word_char(text[index]):
        index += 1
    return index


def word_at(text, index):
    total = len(text)
    if total == 0:
        return 0, 0
    index = max(0, min(index, total - 1))
    if not _word_char(text[index]):
        start = index
        while start > 0 and not _word_char(text[start - 1]) and not text[start - 1].isspace():
            start -= 1
        end = index
        while end < total and not _word_char(text[end]) and not text[end].isspace():
            end += 1
        return start, end
    start = index
    while start > 0 and _word_char(text[start - 1]):
        start -= 1
    end = index
    while end < total and _word_char(text[end]):
        end += 1
    return start, end


def push_history(board):
    board.history.append((board.composer, board.caret, board.anchor))
    if len(board.history) > 200:
        board.history.pop(0)
    board.future.clear()


def undo_text(board):
    if not board.history:
        return
    board.future.append((board.composer, board.caret, board.anchor))
    board.composer, board.caret, board.anchor = board.history.pop()


def redo_text(board):
    if not board.future:
        return
    board.history.append((board.composer, board.caret, board.anchor))
    board.composer, board.caret, board.anchor = board.future.pop()


def editor_layout(board, width):
    """Bar rectangle, text origin and visible line count for the current text."""
    bar_w = max(420.0, min(560.0, width - 100.0))
    field_w = bar_w - 2 * BAR_PAD
    lines = wrap_lines(board.composer, TEXT_SIZE, field_w)
    visible = max(MIN_LINES, min(len(lines), MAX_LINES))
    text_h = visible * LINE_H
    bar_h = text_h + 2 * BAR_PAD + BUTTON_H + 8
    x = (width - bar_w) / 2
    y = 24.0
    board.text_lines = lines
    board.text_visible = visible
    board.text_field_w = field_w
    board.text_x = x + BAR_PAD
    board.text_top = y + bar_h - BAR_PAD
    board.bar_rect = (x, y, bar_w, bar_h)
    board.button_rect = (
        x + bar_w - BAR_PAD - BUTTON_W,
        y + BAR_PAD - 2,
        BUTTON_W,
        BUTTON_H,
    )
    return lines


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
        self.composer = ""
        self.caret = 0
        self.anchor = 0
        self.focused = False
        self.text_scroll = 0.0
        self.text_lines = []
        self.text_visible = 1
        self.text_field_w = 0.0
        self.text_x = 0.0
        self.text_top = 0.0
        self.bar_rect = (0.0, 0.0, 0.0, 0.0)
        self.button_rect = (0.0, 0.0, 0.0, 0.0)
        self.history = []
        self.future = []
        self.strip_hits = []
        self.text_drag = False
        self.text_word_drag = False
        self.text_clicks = (0.0, 0.0, 0.0, 0)
        self.queue = []
        self.running = ""
        self.pending = []
        self.counter = 0
        self.ticks = 0
        self.known = 0
        self.restored = False

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
            self.restored = bool(self.positions)
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


def prompt_for(board, path):
    for entry in board.entries:
        if str(entry.path) == path:
            return entry.prompt or ""
    return ""


def draw_selection_strip(board, width):
    """Small thumbnails of the selection, above the composer."""
    board.strip_hits = []
    selected = [
        path
        for path in board.order
        if path in board.selected and not path.startswith("pending:")
    ]
    if not selected:
        return
    bx, by, bw, bh = board.bar_rect
    y = by + bh + 10
    size = 40.0
    gap = 6.0
    x = bx
    single = len(selected) == 1
    button_w = 46.0
    button_h = 24.0
    reserve = 2 * (button_w + 6) + 12 if single else 0.0
    limit = bx + bw - reserve
    shown = 0
    for path in selected:
        if x + size > limit:
            break
        cached = board.texture(path)
        if cached is not None:
            texture, image_w, image_h = cached
            aspect = image_w / image_h
            if aspect >= 1.0:
                draw_w = size
                draw_h = size / aspect
            else:
                draw_h = size
                draw_w = size * aspect
            draw_rect(x, y, size, size, (0.115, 0.115, 0.125, 1.0))
            draw_image(
                texture,
                x + (size - draw_w) / 2,
                y + (size - draw_h) / 2,
                draw_w,
                draw_h,
            )
        else:
            draw_rect(x, y, size, size, (0.17, 0.17, 0.18, 1.0))
        draw_rect(x, y, size, 1.5, ACCENT)
        board.strip_hits.append(((x, y, size, size), "select", path))
        x += size + gap
        shown += 1
    remaining = len(selected) - shown
    if remaining > 0:
        draw_text(f"+{remaining}", x, y + 14, 11, DIM)
        x += 28
    if not single:
        return
    path = selected[0]
    prompt = prompt_for(board, path)
    use_x = bx + bw - button_w
    copy_x = use_x - button_w - 6
    text_x = x
    text_limit = copy_x - 10 - text_x
    label = clip_text(prompt, 11, text_limit) if prompt else "(no prompt saved)"
    draw_text(label, text_x, y + 15, 11, TEXT if prompt else DIM)
    for offset, (action, caption) in enumerate((("copy", "Copy"), ("use", "Use"))):
        bx0 = copy_x + offset * (button_w + 6)
        draw_rect(bx0, y + 8, button_w, button_h, (0.19, 0.19, 0.2, 1.0))
        draw_text(caption, bx0 + 11, y + 16, 11, TEXT)
        board.strip_hits.append(((bx0, y + 8, button_w, button_h), action, path))


def draw_composer(board, width):
    lines = editor_layout(board, width)
    x, y, bar_w, bar_h = board.bar_rect
    draw_rect(x, y, bar_w, bar_h, (0.105, 0.105, 0.115, 0.97))
    if board.focused:
        draw_rect(x, y, bar_w, 1.5, ACCENT)

    total = len(lines)
    visible = board.text_visible
    first = int(max(0.0, min(board.text_scroll, total - visible)))
    sel_start, sel_end = sorted((board.caret, board.anchor))
    caret_row, _column = caret_line(lines, board.caret)

    for row in range(visible):
        line_index = first + row
        if line_index >= total:
            break
        start, end = lines[line_index]
        baseline = board.text_top - (row + 1) * LINE_H + 4
        if sel_start != sel_end and not (sel_end <= start or sel_start >= end):
            low = max(sel_start, start)
            high = min(sel_end, end)
            x1 = board.text_x + text_width(board.composer[start:low], TEXT_SIZE)
            x2 = board.text_x + text_width(board.composer[start:high], TEXT_SIZE)
            draw_rect(
                x1,
                baseline - 3,
                max(2.0, x2 - x1),
                LINE_H - 2,
                (0.26, 0.34, 0.52, 1.0),
            )
        if start < end:
            draw_text(
                board.composer[start:end],
                board.text_x,
                baseline,
                TEXT_SIZE,
                TEXT,
            )
    if not board.composer:
        draw_text(
            "Describe an image…",
            board.text_x,
            board.text_top - LINE_H + 4,
            TEXT_SIZE,
            DIM,
        )
    if board.focused and (time.time() % 1.0) < 0.55:
        row = caret_row - first
        if 0 <= row < visible:
            start = lines[caret_row][0]
            caret_x = board.text_x + text_width(
                board.composer[start : board.caret], TEXT_SIZE
            )
            caret_y = board.text_top - (row + 1) * LINE_H + 3
            draw_rect(caret_x, caret_y, 1.4, LINE_H - 2, TEXT)

    bx, by, bw, bh = board.button_rect
    state = bpy.context.window_manager.remi_image
    busy = state.busy or bool(board.queue)
    draw_rect(bx, by, bw, bh, (0.24, 0.24, 0.25, 1.0) if busy else ACCENT)
    draw_text(
        "Create",
        bx + 32,
        by + 8,
        12,
        TEXT if busy else (0.06, 0.06, 0.07, 1.0),
    )


def run_strip_action(context, action, path):
    board = BOARD
    if board is None:
        return
    if action == "select":
        board.raise_card(path)
        board.selected = {path}
        store_selection(context, board.selected)
        return
    prompt = prompt_for(board, path)
    if not prompt:
        return
    if action == "copy":
        try:
            context.window_manager.clipboard = prompt
        except Exception:
            pass
        return
    if action == "use":
        board.composer = prompt
        board.caret = board.anchor = len(prompt)
        board.focused = True
        board.text_scroll = 0.0
        editor_layout(board, board.region[0])


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

    draw_composer(board, width)
    draw_selection_strip(board, width)
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


def layout_file():
    override = os.environ.get("REMI_BOARD_LAYOUT")
    if override:
        return Path(override)
    try:
        base = Path(bpy.utils.user_resource("CONFIG")) / "remi"
        base.mkdir(parents=True, exist_ok=True)
        return base / "board_layout.json"
    except Exception:
        return None


def layout_key(context):
    try:
        return str(Path(bpy.path.abspath(context.scene.remi_image.output)).resolve())
    except Exception:
        return "default"


def load_layout(context):
    path = layout_file()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    galleries = data.get("galleries") if isinstance(data, dict) else None
    if not isinstance(galleries, dict):
        return {}
    entry = galleries.get(layout_key(context))
    return entry if isinstance(entry, dict) else {}


def save_layout(context):
    board = BOARD
    if board is None:
        return
    path = layout_file()
    if path is None:
        return
    payload = {
        "positions": {
            str(item): [round(float(pos[0]), 2), round(float(pos[1]), 2)]
            for item, pos in board.positions.items()
            if not str(item).startswith("pending:")
        },
        "scales": {
            str(item): round(float(value), 4)
            for item, value in board.scales.items()
            if not str(item).startswith("pending:")
        },
        "zoom": round(float(board.zoom), 4),
        "pan": [round(float(board.pan[0]), 2), round(float(board.pan[1]), 2)],
    }
    try:
        data = {}
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        galleries = data.get("galleries")
        if not isinstance(galleries, dict):
            galleries = {}
        galleries[layout_key(context)] = payload
        data["galleries"] = galleries
        path.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
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
        if not board.restored:
            board.fit()
        board.composer = context.scene.remi_image.prompt or ""
        board.caret = len(board.composer)
        board.anchor = board.caret
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
        """Window coordinates converted to the region we actually draw in."""
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
                region = next(
                    (item for item in area.regions if item.type == "WINDOW"), None
                )
                if region is not None:
                    self._area_x = area.x + region.x
                    self._area_y = area.y + region.y
                else:
                    self._area_x, self._area_y = area.x, area.y
        return event.mouse_x - self._area_x, event.mouse_y - self._area_y

    def _selection(self, board):
        return sorted((board.caret, board.anchor))

    def _replace_selection(self, board, text):
        low, high = self._selection(board)
        board.composer = board.composer[:low] + text + board.composer[high:]
        board.caret = low + len(text)
        board.anchor = board.caret

    def _refresh_text(self, board):
        editor_layout(board, board.region[0])
        row, _column = caret_line(board.text_lines, board.caret)
        visible = board.text_visible
        if row < board.text_scroll:
            board.text_scroll = float(row)
        elif row >= board.text_scroll + visible:
            board.text_scroll = float(row - visible + 1)

    def _text_event(self, context, board, event):
        if event.value != "PRESS":
            return False
        text = board.composer
        key = event.type
        ctrl = event.ctrl
        shift = event.shift
        if key == "ESC":
            board.focused = False
            return True
        if ctrl and key == "A":
            board.anchor = 0
            board.caret = len(text)
            return True
        if ctrl and key == "Z" and not shift:
            undo_text(board)
            self._refresh_text(board)
            return True
        if (ctrl and key == "Y") or (ctrl and shift and key == "Z"):
            redo_text(board)
            self._refresh_text(board)
            return True
        if ctrl and key in {"C", "X"}:
            low, high = self._selection(board)
            selected = text[low:high]
            if selected:
                try:
                    context.window_manager.clipboard = selected
                except Exception:
                    pass
                if key == "X":
                    push_history(board)
                    board.composer = text[:low] + text[high:]
                    board.caret = board.anchor = low
                    self._refresh_text(board)
            return True
        if ctrl and key == "V":
            try:
                clip = context.window_manager.clipboard or ""
            except Exception:
                clip = ""
            if clip:
                push_history(board)
                self._replace_selection(board, clip)
                self._refresh_text(board)
            return True
        if key == "RETURN":
            if shift:
                push_history(board)
                self._replace_selection(board, "\n")
                self._refresh_text(board)
            else:
                submit_current(context)
            return True
        if ctrl and key in {"BACK_SPACE", "DELETE"}:
            low, high = self._selection(board)
            push_history(board)
            if low != high:
                board.composer = text[:low] + text[high:]
                board.caret = board.anchor = low
            elif key == "BACK_SPACE":
                start = word_left(text, board.caret)
                board.composer = text[:start] + text[board.caret :]
                board.caret = board.anchor = start
            else:
                end = word_right(text, board.caret)
                board.composer = text[: board.caret] + text[end:]
                board.caret = board.anchor = board.caret
            self._refresh_text(board)
            return True
        if key == "BACK_SPACE":
            low, high = self._selection(board)
            if low != high:
                push_history(board)
                board.composer = text[:low] + text[high:]
                board.caret = board.anchor = low
            elif board.caret > 0:
                push_history(board)
                board.composer = text[: board.caret - 1] + text[board.caret :]
                board.caret -= 1
                board.anchor = board.caret
            self._refresh_text(board)
            return True
        if key == "DELETE":
            low, high = self._selection(board)
            if low != high:
                push_history(board)
                board.composer = text[:low] + text[high:]
                board.caret = board.anchor = low
            elif board.caret < len(text):
                push_history(board)
                board.composer = text[: board.caret] + text[board.caret + 1 :]
            self._refresh_text(board)
            return True
        if key in {"LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW", "DOWN_ARROW", "HOME", "END"}:
            lines = board.text_lines or wrap_lines(text, TEXT_SIZE, board.text_field_w)
            if key in {"LEFT_ARROW", "RIGHT_ARROW"}:
                step = -1 if key == "LEFT_ARROW" else 1
                if ctrl:
                    board.caret = (
                        word_left(text, board.caret)
                        if step < 0
                        else word_right(text, board.caret)
                    )
                else:
                    board.caret = max(0, min(len(text), board.caret + step))
            elif key in {"HOME", "END"}:
                if ctrl:
                    board.caret = 0 if key == "HOME" else len(text)
                else:
                    row, _column = caret_line(lines, board.caret)
                    start, end = lines[row]
                    board.caret = start if key == "HOME" else end
            else:
                row, _column = caret_line(lines, board.caret)
                target = max(0, min(len(lines) - 1, row + (-1 if key == "UP_ARROW" else 1)))
                start, end = lines[target]
                offset = board.caret - lines[row][0]
                board.caret = min(start + offset, end)
            if not shift:
                board.anchor = board.caret
            self._refresh_text(board)
            return True
        char = getattr(event, "unicode", "")
        if char and char.isprintable() and not ctrl and not event.alt:
            push_history(board)
            self._replace_selection(board, char)
            self._refresh_text(board)
            return True
        return False

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

        if board.focused:
            if self._text_event(context, board, event):
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

        if event.type == "ESC" and event.value == "PRESS":
            self._finish(context)
            return {"CANCELLED"}

        if event.type == "MOUSEMOVE":
            if board.text_drag and board.focused:
                caret = caret_from_point(
                    board.text_lines,
                    board.composer,
                    mx,
                    my,
                    TEXT_SIZE,
                    board.text_x,
                    board.text_top,
                )
                if board.text_word_drag:
                    start, end = word_at(board.composer, caret)
                    board.caret = end if caret >= board.anchor else start
                else:
                    board.caret = caret
                self._redraw(context)
                return {"RUNNING_MODAL"}
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
            editor_layout(board, board.region[0])
            bx, by, bw, bh = board.bar_rect
            over_bar = bx <= mx <= bx + bw and by <= my <= by + bh
            if board.focused and over_bar and len(board.text_lines) > board.text_visible:
                step = -1 if event.type == "WHEELUPMOUSE" else 1
                limit = len(board.text_lines) - board.text_visible
                board.text_scroll = max(0.0, min(float(limit), board.text_scroll + step))
                self._redraw(context)
                return {"RUNNING_MODAL"}
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
            for rect, action, target in board.strip_hits:
                if (
                    rect[0] <= mx <= rect[0] + rect[2]
                    and rect[1] <= my <= rect[1] + rect[3]
                ):
                    run_strip_action(context, action, target)
                    self._redraw(context)
                    return {"RUNNING_MODAL"}
            if not board.bar_rect[2]:
                editor_layout(board, board.region[0])
            bx, by, bw, bh = board.bar_rect
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                btx, bty, btw, bth = board.button_rect
                if btx <= mx <= btx + btw and bty <= my <= bty + bth:
                    submit_current(context)
                else:
                    board.focused = True
                    caret = caret_from_point(
                        board.text_lines,
                        board.composer,
                        mx,
                        my,
                        TEXT_SIZE,
                        board.text_x,
                        board.text_top,
                    )
                    last_x, last_y, last_time, count = board.text_clicks
                    now = time.time()
                    if now - last_time < 0.4 and abs(mx - last_x) < 8 and abs(my - last_y) < 8:
                        count = min(count + 1, 3)
                    else:
                        count = 1
                    board.text_clicks = (mx, my, now, count)
                    if count == 2:
                        start, end = word_at(board.composer, caret)
                        board.anchor, board.caret = start, end
                    elif count >= 3:
                        lines = board.text_lines or wrap_lines(
                            board.composer, TEXT_SIZE, board.text_field_w
                        )
                        row, _column = caret_line(lines, caret)
                        start, end = lines[row]
                        board.anchor, board.caret = start, end
                    elif event.shift:
                        board.caret = caret
                    else:
                        board.caret = board.anchor = caret
                    board.text_drag = True
                    board.text_word_drag = count == 2
                    self._refresh_text(board)
                    if os.environ.get("REMI_BOARD_DEBUG"):
                        print(
                            f"board click mx={mx:.1f} my={my:.1f} "
                            f"bar={tuple(round(v) for v in board.bar_rect)} "
                            f"text_top={board.text_top:.1f} caret={caret} "
                            f"len={len(board.composer)}"
                        )
                self._redraw(context)
                return {"RUNNING_MODAL"}
            board.focused = False
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
            board.text_drag = False
            board.text_word_drag = False
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
    """Queue whatever is typed in the board's composer."""
    board = BOARD
    if board is None:
        return False
    text = board.composer.strip()
    if not text:
        return False
    context.scene.remi_image.prompt = text
    board.add_pending(text)
    board.queue.append(text)
    board.composer = ""
    board.caret = 0
    board.anchor = 0
    board.text_scroll = 0.0
    board.focused = False
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


class REMI_BOARD_OT_workspace(Operator):
    """Create or switch to a Remi workspace tab."""

    bl_idname = "remi_board.workspace"
    bl_label = "Open Remi Workspace"
    bl_description = "Switch to a Remi workspace, creating it the first time"
    bl_options = {"REGISTER"}

    def execute(self, context):
        window = context.window
        name = "Remi"
        existing = bpy.data.workspaces.get(name)
        if existing is not None:
            if window is not None:
                window.workspace = existing
            return {"FINISHED"}
        before = {workspace.name for workspace in bpy.data.workspaces}
        try:
            # add() only opens the add-workspace menu; duplicate() creates the tab.
            bpy.ops.workspace.duplicate()
        except Exception as exc:
            self.report({"ERROR"}, f"Could not duplicate a workspace: {exc}")
            return {"CANCELLED"}
        created = next(
            (item for item in bpy.data.workspaces if item.name not in before), None
        )
        if created is None:
            self.report({"ERROR"}, "The workspace was not created")
            return {"CANCELLED"}
        created.name = name
        if window is None:
            return {"FINISHED"}
        window.workspace = created
        screen = window.screen
        if screen is not None:
            areas = list(screen.areas)
            if areas:
                main = max(areas, key=lambda item: item.width * item.height)
                if main.type != "VIEW_3D":
                    main.type = "VIEW_3D"
                space = main.spaces.active
                if hasattr(space, "show_region_ui"):
                    space.show_region_ui = True
        self.report({"INFO"}, "Remi workspace ready")
        return {"FINISHED"}


class REMI_MT_flow(Menu):
    bl_idname = "REMI_MT_flow"
    bl_label = "Remi"

    def draw(self, context):
        layout = self.layout
        layout.operator("remi_board.workspace", text="Remi Workspace", icon="WINDOW")
        layout.separator()
        layout.operator("remi_board.open", text="Image Board in New Window", icon="IMAGE_DATA")


def topbar_flow_menu(self, context):
    self.layout.menu("REMI_MT_flow")


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
    REMI_BOARD_OT_workspace,
    REMI_BOARD_MT_image,
    REMI_MT_flow,
    REMI_BOARD_PT_panel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    try:
        bpy.types.TOPBAR_MT_editor_menus.remove(topbar_flow_menu)
    except Exception:
        pass
    bpy.types.TOPBAR_MT_editor_menus.append(topbar_flow_menu)


def unregister():
    global BOARD
    try:
        bpy.types.TOPBAR_MT_editor_menus.remove(topbar_flow_menu)
    except Exception:
        pass
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
