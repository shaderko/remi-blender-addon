"""Remi's image/3D canvas, hosted in one native Image Editor region.

File-backed assets and cached previews belong to the canvas, not hidden scene
objects. Switching workspaces keeps jobs running; unloading cancels its workers
without removing source files, completed versions, or explicit scene imports.
"""

from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
import json
import math
import os
import shutil
import textwrap
import tempfile
import time
import uuid
from pathlib import Path

import blf
import bpy
import gpu
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, StringProperty
from bpy.app.handlers import persistent
from bpy.types import FileHandler, Menu, Operator, OperatorFileListElement
from bpy_extras.io_utils import ExportHelper
from gpu_extras.batch import batch_for_shader

from ...blender import gaussian_splats
from ..images.gallery import gallery
from .assets import Catalog, OBJECT_ASSET_KEY, OBJECT_REVISION_KEY, SCENE_ASSETS_KEY
from .jobs import Runner, generation_options
from .splat_preview import NativePreview, NativeMeshPreview, Preview
from . import texture_effects, toolbar

SELECTION_KEY = "remi_board_selection"

CELL_W = 300.0
CELL_H = 300.0
GAP = 30.0
COLUMNS = 4
THUMB = 512
RESIZE_PX = 16.0
MIN_SCALE = 0.25
MAX_SCALE = 4.0
MAX_LOADED_PREVIEWS = 3
PREVIEW_IDLE_SECONDS = 30.0

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
_native_drop_override = None


def gallery_entries(context):
    try:
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
        # Image textures are scene-linear and the native window's sRGB target
        # performs the display conversion. Built-in UI IMAGE shaders assume
        # display-encoded inputs; an extra conversion here washes photos out.
        interface = gpu.types.GPUStageInterfaceInfo("remi_board_image")
        interface.smooth("VEC2", "uv")
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, "VEC2", "pos")
        info.vertex_in(1, "VEC2", "texCoord")
        info.vertex_out(interface)
        info.push_constant("MAT4", "matrix")
        info.push_constant("FLOAT", "premultiplied")
        info.sampler(0, "FLOAT_2D", "image")
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source("void main() { gl_Position=matrix*vec4(pos,0.0,1.0); uv=texCoord; }")
        info.fragment_source("""
void main() {
    vec4 color = texture(image, uv);
    if (premultiplied > 0.5 && color.a > 0.00001) { color.rgb /= color.a; }
    fragColor = color;
}
""")
        _IMAGE = gpu.shader.create_from_info(info)
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


def draw_image(texture, x, y, w, h, premultiplied=False):
    gpu.state.blend_set("ALPHA")
    shader = image_shader()
    shader.bind()
    shader.uniform_sampler("image", texture)
    shader.uniform_float("matrix", gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
    shader.uniform_float("premultiplied", float(premultiplied))
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


@lru_cache(maxsize=1024)
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
    # Long generation prompts must not trigger thousands of font measurements
    # on every mouse movement. Find the visible prefix in logarithmic work.
    low, high = 0, len(text)
    while low < high:
        middle = (low+high+1)//2
        if text_width(text[:middle] + "…", size) <= limit:
            low = middle
        else:
            high = middle-1
    return text[:low] + "…"


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
    left = min(toolbar.LEFT_MARGIN, max(0.0, width-100))
    bar_w = max(1.0, min(680.0, width-left-32.0))
    field_w = max(24.0, bar_w - 2 * BAR_PAD)
    lines = wrap_lines(board.composer, TEXT_SIZE, field_w)
    visible = max(MIN_LINES, min(len(lines), MAX_LINES))
    text_h = visible * LINE_H
    bar_h = text_h + 2 * BAR_PAD + BUTTON_H + 8
    x = left + (width-left-bar_w) / 2
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


def focus_image(board, path):
    """Inspect a card in place; Esc restores the previous board view."""
    if path not in board.positions:
        return False
    if board.image_focus is None:
        board.image_focus = (board.zoom, list(board.pan))
    x, y = board.positions[path]
    size = CELL_W * board.scale_of(path)
    width, height = board.region
    board.zoom = max(0.12, min((width - 48) / size, (height - 180) / size, 3.0))
    board.pan = [(width - size * board.zoom) / 2 - x * board.zoom,
                 (height - size * board.zoom) / 2 - y * board.zoom + 50]
    return True


def board_window(board):
    return next((window for window in bpy.context.window_manager.windows
                 if window.as_pointer() == board.window), None)


def board_modal_running(window):
    # Blender can expose a null entry briefly after an add-on's modal class is
    # unregistered. It must not prevent clean re-enablement or UI recovery.
    return any(operator is not None and operator.bl_rna.identifier == "REMI_BOARD_OT_run"
               for operator in window.modal_operators)


def board_area(board, window=None):
    """Find the active host space, including Blender's temporary maximized screen."""
    window = window or board_window(board)
    if (window is None or window.workspace.as_pointer() != board.workspace
            or window.scene != board.scene):
        return None
    return next((area for area in window.screen.areas
                 if area.type == "IMAGE_EDITOR"
                 and area.spaces.active.as_pointer() == board.space), None)


def window_region(area):
    return next((region for region in area.regions if region.type == "WINDOW"), None)


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
    def __init__(self, context):
        self.window = context.window.as_pointer()
        self.workspace = context.workspace.as_pointer()
        self.workspace_uid = context.workspace.session_uid
        self.workspace_name = context.workspace.name
        # Maximize Area swaps areas/screens, but preserves the editor space.
        self.space = context.space_data.as_pointer()
        self.scene = context.scene
        self.view_layer = context.view_layer
        self.zoom = 0.8
        self.pan = [120.0, 620.0]
        self.entries = []
        self.catalog = Catalog()
        self.runner = Runner()
        self.order = []
        self.positions = {}
        self.scales = {}
        self.selected = set()
        self.hover = None
        self.textures = {}
        self.texture_images = {}
        self.region = (1440, 810)
        self.pan_drag = None
        self.card_drag = None
        self.box_drag = None
        self.resize_drag = None
        self.last_navigation = 0.0
        self.next_service = 0.0
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
        self.toolbar_hover = ""
        self.toolbar_since = 0.0
        self.toolbar_pressed = ""
        self.loaded_previews = OrderedDict()
        self.live_asset_id = ""
        self.dirty_previews = set()
        self.preview_errors = {}
        self.thumbnail_checks = {}
        self.link_drag = None  # Direct 3D-output -> Mesh-input connection drag.
        self.texture_drop = None
        self.texture_effects = {}
        self.orbit_drag = None
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
        self.image_focus = None

    # data ---------------------------------------------------------------
    def load(self, context, entries):
        layout = load_layout(context)
        documents = layout.get("assets", [])
        try:
            saved_scene = json.loads(context.scene.get(SCENE_ASSETS_KEY, "{}"))
            if saved_scene.get("gallery") == layout_key(context):
                documents = saved_scene.get("assets", documents)
        except (ValueError, TypeError, AttributeError):
            pass
        self.catalog = Catalog(documents if isinstance(documents, list) else [])
        self.entries = entries
        self.known = len(entries)
        images = [self.catalog.image(entry.path, entry.prompt) for entry in entries]
        aliases = {asset.artifact: asset.id for asset in images}
        self.order = [asset.id for asset in images if not asset.hidden]
        self.order += [asset.id for asset in self.catalog.assets.values() if asset.kind != "IMAGE" and not asset.hidden]
        self.positions = {}
        saved = layout.get("positions") if isinstance(layout, dict) else None
        paths = set(self.order)
        if isinstance(saved, dict):
            for path, position in saved.items():
                path = aliases.get(str(path), str(path))
                if path not in paths:
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
                        self.scales[aliases.get(str(path), str(path))] = max(MIN_SCALE, min(MAX_SCALE, float(value)))
                    except (TypeError, ValueError):
                        continue
            for path in self.order:
                if path not in self.positions:
                    self.place_asset(path, self.catalog.assets[path].parent)
        else:
            for index, asset in enumerate(images):
                if not asset.hidden:
                    self.positions[asset.id] = self.grid_position(index)
            for path in self.order:
                if path not in self.positions:
                    self.place_asset(path, self.catalog.assets[path].parent)
        self.migrate_scene_splats(context)
        self.discover_generation_files(context)
        saved_order = layout.get("order", [])
        if isinstance(saved_order, list):
            saved_order = [aliases.get(key, key) for key in saved_order if isinstance(key, str)]
            self.order = list(dict.fromkeys([key for key in saved_order if key in self.order] + self.order))

    def migrate_scene_splats(self, context):
        """Keep old sketch results, including renamed objects/missing archives."""
        by_image = {asset.artifact: asset for asset in self.catalog.assets.values() if asset.kind == "IMAGE"}
        for obj in context.scene.objects:
            if not gaussian_splats.is_splat_object(obj):
                continue
            parent = by_image.get(obj.get("remi_source_image", ""))
            if parent is None or obj.get(OBJECT_ASSET_KEY) in self.catalog.assets:
                continue
            asset = self.catalog.derive(parent.id, "SPLAT", "GENERATE_3D", title="3D source")
            archive = Path(obj.get("remi_splat_archive", ""))
            try:
                if not archive.is_file():
                    directory = self.output_directory() / asset.id.split(":")[-1]
                    directory.mkdir(parents=True, exist_ok=True)
                    archive = gaussian_splats.write_splat_archive(obj, directory / "splats.npz")
                asset.artifact = str(archive.resolve())
                asset.state, asset.status = "READY", "Ready"
                obj[OBJECT_ASSET_KEY] = asset.id
                self.place_asset(asset.id, parent.id)
                self.order.append(asset.id)
            except Exception as exc:
                self.catalog.assets.pop(asset.id, None)
                print(f"Remi board: could not migrate {obj.name}: {exc}")

    def discover_generation_files(self, context):
        """Recover old file-backed results even when they were never added/saved."""
        root = Path(bpy.path.abspath(context.scene.remi_generation.output))
        parents = {asset.artifact: asset.id for asset in self.catalog.assets.values() if asset.kind == "IMAGE"}
        known = {asset.artifact for asset in self.catalog.assets.values() if asset.kind == "SPLAT"}
        if not root.is_dir():
            return
        from .assets import Asset
        for index, directory in enumerate(root.iterdir()):
            if index >= 10000:
                break
            archive, meta = directory / "splats.npz", directory / "meta.json"
            try:
                if not archive.is_file() or not (directory / "complete.json").is_file() or not meta.is_file() or meta.stat().st_size > 128*1024:
                    continue
                artifact = str(archive.resolve())
                if artifact in known:
                    continue
                data = json.loads(meta.read_text(encoding="utf-8"))
                source = str(Path(data.get("image", "")).resolve())
                if source not in parents:
                    continue
                identifier = "asset:" + uuid.uuid5(uuid.NAMESPACE_URL, archive.resolve().as_uri()).hex
                asset = Asset(identifier, "SPLAT", artifact=artifact, parent=parents[source],
                              title="3D source", operation="GENERATE_3D", directory=str(directory),
                              settings={key: data[key] for key in ("seed", "gaussians", "model") if key in data})
                self.catalog.assets[identifier] = asset
                known.add(artifact)
                self.place_asset(identifier, asset.parent)
                self.order.append(identifier)
            except (OSError, ValueError, TypeError, AttributeError):
                continue

    def output_directory(self):
        return Path(bpy.path.abspath(self.scene.remi_image.output)) / ".remi-board"

    def place_asset(self, identifier, parent=""):
        if parent in self.positions:
            x, y = self.positions[parent]
            x += CELL_W*self.scale_of(parent) + GAP
            scale = self.scale_of(parent)
            self.scales[identifier] = scale
            size = CELL_W*scale
            while any(abs(x-p[0]) < size+GAP/2 and abs(y-p[1]) < size+GAP/2 for p in self.positions.values()):
                y -= size + GAP
            self.positions[identifier] = [x, y]
        else:
            self.positions[identifier] = self.new_slot()

    def derive(self, parent, kind, operation, settings, title):
        asset = self.catalog.derive(parent, kind, operation, settings, title)
        asset.yaw = self.catalog.assets[parent].yaw
        asset.elevation = self.catalog.assets[parent].elevation
        self.place_asset(asset.id, parent)
        self.order.append(asset.id)
        self.selected = {asset.id}
        self.scene[SELECTION_KEY] = asset.id
        # Reveal the new result, but never rearrange existing user-placed cards.
        x, y, w, h = self.card_rect(asset.id)
        width, height = self.region
        self.pan[0] += max(0, 24-x) - max(0, x+w-width+24)
        self.pan[1] += max(0, 150-y) - max(0, y+h-height+24)
        return asset

    @staticmethod
    def grid_position(index):
        column = index % COLUMNS
        row = index // COLUMNS
        # Leave room for a derived card alongside each new source image.
        return [column * 2 * (CELL_W + GAP), -(row * (CELL_H + GAP))]

    def new_slot(self):
        if not self.positions:
            return [0.0, 0.0]
        left = min(position[0] for position in self.positions.values())
        bottom = min(position[1] for position in self.positions.values())
        return [left, bottom - CELL_H - GAP]

    def add_pending_card(self, prefix="pending:"):
        key = f"{prefix}{self.counter}"
        self.counter += 1
        self.positions[key] = self.new_slot()
        self.order.append(key)
        return key

    def add_pending(self, prompt):
        key = self.add_pending_card("pending:")
        self.pending.append({"key": key, "prompt": prompt})
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
        self.unload_preview(path)
        self.release_texture(path)
        self.dirty_previews.discard(path)

    def reload(self, context):
        entries = gallery_entries(context)
        self.entries = entries
        for entry in entries:
            asset = self.catalog.image(entry.path, entry.prompt)
            path = asset.id
            if asset.hidden:
                continue
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
        self.known = len(entries)
        self.migrate_scene_splats(context)
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

    def output_handle(self, path):
        x, y, w, h = self.card_rect(path)
        return x+w, y+h/2

    def input_handle(self, path):
        x, y, _w, h = self.card_rect(path)
        return x, y+h/2

    def link_source_at(self, mx, my):
        for path in reversed(self.order):
            asset = self.catalog.assets.get(path)
            if asset is None or asset.kind != "SPLAT" or asset.state != "READY":
                continue
            hx, hy = self.output_handle(path)
            if abs(mx-hx) <= 11 and abs(my-hy) <= 11:
                return path
        return None

    def resize_target(self, mx, my):
        for path in reversed(self.order):
            hx, hy = self.corner_handle(path)
            if abs(mx - hx) <= RESIZE_PX and abs(my - hy) <= RESIZE_PX:
                return path
        return None

    def hit(self, mx, my, exclude=()):
        for path in reversed(self.order):
            if path in exclude:
                continue
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

    def update_box_selection(self, mx, my):
        drag = self.box_drag
        drag["end"] = (mx, my)
        x0, y0 = drag["start"]
        left, right = sorted((x0, mx))
        bottom, top = sorted((y0, my))
        hit = set()
        if right-left >= 4 or top-bottom >= 4:
            for identifier in self.order:
                x, y, w, h = self.card_rect(identifier)
                if x <= right and x+w >= left and y <= top and y+h >= bottom:
                    hit.add(identifier)
        if drag["mode"] == "ADD":
            self.selected = drag["selection"] | hit
        elif drag["mode"] == "REMOVE":
            self.selected = drag["selection"] - hit
        else:
            self.selected = hit

    def navigating(self):
        return (self.pan_drag is not None or self.card_drag is not None
                or self.box_drag is not None or self.resize_drag is not None
                or time.monotonic()-self.last_navigation < 0.2)

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
        asset = self.catalog.assets.get(path)
        if asset is None:
            return None
        loaded = self.loaded_previews.get(path) if asset.kind in {"SPLAT", "MESH"} else None
        if loaded is not None and loaded["preview"] is not None:
            return loaded["preview"].texture, 512, 512, True
        source = asset.artifact if asset.kind == "IMAGE" else asset.preview
        if not source or asset.state != "READY":
            return None
        key = (path, source)
        return self.textures.get(key)

    def load_texture(self, path):
        """One idle-time upload; never decode files inside the draw handler."""
        asset = self.catalog.assets.get(path)
        if asset is None or asset.state != "READY":
            return False
        source = asset.artifact if asset.kind == "IMAGE" else asset.preview
        key = (path, source)
        if not source or key in self.textures:
            return False
        image = None
        try:
            image = bpy.data.images.load(source, check_existing=False)
            width, height = image.size
            if max(width, height) > THUMB:
                scale = THUMB / max(width, height)
                image.scale(max(1, int(width * scale)), max(1, int(height * scale)))
                width, height = image.size
            texture = gpu.texture.from_image(image)
        except Exception:
            if image is not None:
                bpy.data.images.remove(image)
            self.textures[key] = None
            self.preview_errors[path] = f"Could not load preview: {source}"
            return True
        self.texture_images[key] = image
        entry = (texture, max(1, width), max(1, height), False)
        self.textures[key] = entry
        return True

    def invalidate_preview(self, identifier):
        self.dirty_previews.add(identifier)
        self.preview_errors.pop(identifier, None)
        self.thumbnail_checks.pop(identifier, None)
        if self.orbit_drag is None or self.orbit_drag["path"] != identifier:
            self.unload_preview(identifier)
        asset = self.catalog.assets.get(identifier)
        if (asset is not None and asset.kind == "MESH"
                and (self.orbit_drag is None or self.orbit_drag["path"] != identifier)):
            asset.preview = ""
            asset.status = "Ready"

    def release_texture(self, identifier):
        for key in [key for key in self.textures if key[0] == identifier]:
            self.textures.pop(key, None)
            image = self.texture_images.pop(key, None)
            if image is not None:
                bpy.data.images.remove(image)

    def thumbnail_ready(self, asset):
        key = (asset.preview, asset.yaw, asset.elevation)
        if self.thumbnail_checks.get(asset.id) == key:
            return True
        ready = (bool(asset.preview) and asset.preview_view == [asset.yaw, asset.elevation]
                 and Path(asset.preview).is_file())
        if ready:
            self.thumbnail_checks[asset.id] = key
        return ready

    def capture_thumbnail(self, asset, preview):
        path = self.output_directory() / "previews" / (asset.id.split(":")[-1] + ".png")
        preview.save(path)
        self.release_texture(asset.id)
        asset.preview = str(path)
        asset.preview_view = [asset.yaw, asset.elevation]
        self.thumbnail_checks[asset.id] = (asset.preview, asset.yaw, asset.elevation)
        self.dirty_previews.discard(asset.id)
        self.preview_errors.pop(asset.id, None)
        save_layout(bpy.context)

    def unload_preview(self, identifier):
        entry = self.loaded_previews.pop(identifier, None)
        if entry is not None and entry["preview"] is not None:
            entry["preview"].free()
        if self.live_asset_id == identifier:
            self.live_asset_id = ""

    def expire_previews(self, now=None):
        now = time.monotonic() if now is None else now
        active = self.orbit_drag["path"] if self.orbit_drag is not None else ""
        changed = False
        for identifier, entry in list(self.loaded_previews.items()):
            if identifier != active and now-entry["last_used"] >= PREVIEW_IDLE_SECONDS:
                self.unload_preview(identifier)
                changed = True
        return changed

    def update_live_preview(self, identifier):
        asset = self.catalog.assets[identifier]
        if self.live_asset_id != identifier:
            self.finish_live_preview()
        entry = self.loaded_previews.get(identifier)
        created = entry is None
        if entry is None:
            # Evict before loading, so even transient peak usage respects 3.
            while len(self.loaded_previews) >= MAX_LOADED_PREVIEWS:
                self.unload_preview(next(iter(self.loaded_previews)))
            if asset.kind == "MESH":
                # A background thumbnail from the previous view must not replace
                # the interactive viewport's final capture when it finishes.
                if self.runner.preview_id == identifier and self.runner.preview_job is not None:
                    self.runner.preview_job.cancel()
                    self.runner.preview_job, self.runner.preview_id = None, ""
                preview = NativeMeshPreview(asset.artifact, yaw=asset.yaw, elevation=asset.elevation)
            else:
                from ...integrations.triposplat.splat_data import load
                preview = NativePreview(load(asset.artifact), yaw=asset.yaw, elevation=asset.elevation)
            entry = {"preview": preview,
                     "view": (asset.yaw, asset.elevation),
                     "last_used": time.monotonic()}
            self.loaded_previews[identifier] = entry
        self.live_asset_id = identifier
        view = (asset.yaw, asset.elevation)
        changed = entry["view"] != view
        if changed:
            entry["preview"].render(yaw=asset.yaw, elevation=asset.elevation)
            entry["view"] = view
            entry["last_used"] = time.monotonic()
        self.loaded_previews.move_to_end(identifier)
        return created or changed

    def finish_live_preview(self):
        if not self.live_asset_id:
            return
        identifier = self.live_asset_id
        try:
            self.update_live_preview(identifier)
            entry = self.loaded_previews[identifier]
            self.capture_thumbnail(self.catalog.assets[identifier], entry["preview"])
            entry["last_used"] = time.monotonic()
        except Exception as exc:
            self.preview_errors[identifier] = str(exc)
            self.unload_preview(identifier)
        finally:
            self.live_asset_id = ""

    def prepare_preview(self):
        # Only an orbit can load a live preview. Recently used previews stay
        # warm for 30 seconds, bounded to 3; drawing a thumbnail never touches
        # their idle timers. Off-board thumbnail textures are released too.
        if self.orbit_drag is not None:
            identifier = self.orbit_drag["path"]
            try:
                return self.update_live_preview(identifier)
            except Exception as exc:
                self.preview_errors[identifier] = str(exc)
                self.unload_preview(identifier)
            return False
        if self.navigating():
            return False
        for key in list(self.texture_images):
            identifier = key[0]
            x, y, w, h = self.card_rect(identifier)
            if identifier not in self.selected and (x+w < 0 or x > self.region[0] or y+h < 0 or y > self.region[1]):
                self.release_texture(identifier)
        priority = list(self.selected) + [key for key in self.order if key not in self.selected]
        for key in priority:
            asset = self.catalog.assets.get(key)
            if asset is None or asset.state != "READY" or key in self.preview_errors:
                continue
            x, y, w, h = self.card_rect(key)
            if key not in self.selected and (x+w < 0 or x > self.region[0] or y+h < 0 or y > self.region[1]):
                continue
            try:
                if asset.kind == "SPLAT" and (not self.thumbnail_ready(asset) or key in self.dirty_previews):
                    # A one-time thumbnail capture must respect the same peak
                    # model limit as an interactive preview.
                    while len(self.loaded_previews) >= MAX_LOADED_PREVIEWS:
                        self.unload_preview(next(iter(self.loaded_previews)))
                    from ...integrations.triposplat.splat_data import load
                    preview = Preview(load(asset.artifact), yaw=asset.yaw, elevation=asset.elevation)
                    try:
                        self.capture_thumbnail(asset, preview)
                    finally:
                        preview.free()
                    return True
                if asset.kind == "MESH" and not asset.preview:
                    if asset.status.startswith("Preview unavailable"):
                        continue
                    running = self.runner.preview_job
                    self.runner.request_mesh_preview(asset, self.output_directory())
                    if running is None and self.runner.preview_job is not None:
                        return True
                    continue
                if self.load_texture(key):
                    return True
            except Exception as exc:
                self.preview_errors[key] = str(exc)
                asset.status = f"Preview unavailable: {exc}"
        return False

    def cleanup(self):
        for identifier in list(self.loaded_previews):
            self.unload_preview(identifier)
        for image in self.texture_images.values():
            try:
                bpy.data.images.remove(image)
            except Exception:
                pass
        self.texture_images.clear()
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
    for asset in board.catalog.ancestors(path):
        if asset.kind == "IMAGE":
            return asset.title
    return ""


def draw_selection_strip(board, width):
    """Selection and status only. Actions stay in the fixed left toolbar."""
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
    limit = bx + bw*0.5
    shown = 0
    for path in selected:
        if x + size > limit:
            break
        cached = board.texture(path)
        if cached is not None:
            texture, image_w, image_h, premultiplied = cached
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
                premultiplied,
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
        draw_text(f"{len(selected)} selected", x+8, y+15, 11, DIM)
        return
    path = selected[0]
    asset = board.catalog.assets.get(path)
    prompt = (asset.status if asset and asset.state != "READY" else prompt_for(board, path))
    text_limit = bx+bw-10-x
    title = (asset.title or {"IMAGE": "Image", "SPLAT": "3D source", "MESH": "Mesh"}[asset.kind]) if asset else "Selected asset"
    label = title + " · " + prompt if prompt and prompt != title else title
    label = clip_text(label, 11, max(40.0, text_limit))
    draw_text(label, x, y + 15, 11, TEXT if prompt else DIM)
    if asset and (asset.state in {"ERROR", "INTERRUPTED"} or path in board.preview_errors or asset.status.startswith("Preview unavailable")):
        message = clip_text(asset.status, 11, bw)
        draw_text(message, bx, y + size + 8, 11, (1.0, 0.55, 0.35, 1.0))


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
    ready = bool(board.composer.strip())
    draw_rect(bx, by, bw, bh, ACCENT if ready else (0.20, 0.20, 0.21, 1.0))
    draw_text(
        "Create Image",
        bx + (bw-text_width("Create Image", 12))/2,
        by + 8,
        12,
        (0.06, 0.06, 0.07, 1.0) if ready else DIM,
    )
    if bar_w > 360:
        draw_text("Enter: create · Shift+Enter: new line", x+BAR_PAD, by+9, 10, DIM)


def remove_draw_handlers(board):
    for space_type, attribute in ((bpy.types.SpaceImageEditor, "_handle_image"),):
        handle = getattr(board, attribute, None)
        if handle is None:
            continue
        try:
            space_type.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass
        setattr(board, attribute, None)


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def import_image_file(context, source):
    """Register an external image on the board by giving it a gallery job."""
    source = Path(str(source))
    if not source.is_file():
        return None
    output = Path(bpy.path.abspath(context.scene.remi_image.output))
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    stamp = time.strftime("%Y%m%d_%H%M%S")
    job = output / f"imported_{stamp}_{uuid.uuid4().hex[:8]}"
    try:
        job.mkdir()
        target = job / source.name
        shutil.copy2(source, target)
        meta = {
            "prompt": "",
            "negative_prompt": "",
            "model": "imported",
            "model_label": "Imported",
            "seed": 0,
            "width": 0,
            "height": 0,
            "images": [{"file": target.name, "seed": 0}],
        }
        (job / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (job / "complete.json").write_text("{}", encoding="utf-8")
    except OSError:
        return None
    return target


def incoming_folder(context):
    try:
        output = Path(bpy.path.abspath(context.scene.remi_image.output))
        folder = output / ".incoming"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    except OSError:
        return None


def drain_incoming(context):
    """Import anything dropped into the watched folder."""
    folder = incoming_folder(context)
    if folder is None:
        return 0
    added = 0
    for path in list(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if import_image_file(context, path) is not None:
            try:
                path.unlink()
            except OSError:
                pass
            added += 1
    return added


def paste_image(context):
    """Paste the clipboard image into the board's Image Editor, then import it."""
    window = board_window(BOARD) if BOARD is not None else None
    area = board_area(BOARD, window) if window is not None else None
    if area is None:
        return False
    try:
        with context.temp_override(window=window, screen=window.screen, area=area):
            bpy.ops.image.paste()
    except Exception:
        return False
    space = area.spaces.active
    image = getattr(space, "image", None)
    if image is None:
        return False
    try:
        temporary = Path(bpy.app.tempdir or tempfile.gettempdir())
        temporary = temporary / f"remi_paste_{int(time.time())}.png"
        image.filepath_raw = str(temporary)
        image.file_format = "PNG"
        image.save()
    except Exception:
        return False
    return import_image_file(context, temporary) is not None


def start_splat(context):
    """Queue a new, separately identified 3D asset. Never replace its image."""
    board = BOARD
    if board is None or len(board.selected) != 1:
        return False
    parent = board.catalog.assets.get(next(iter(board.selected)))
    if parent is None or parent.kind != "IMAGE" or parent.state != "READY":
        return False
    board.derive(parent.id, "SPLAT", "GENERATE_3D", generation_options(context), "3D source")
    save_layout(context)
    return True


def add_asset_to_scene(context, identifier):
    board = BOARD
    if board is None:
        return False
    asset = board.catalog.assets.get(identifier)
    if asset is None or asset.kind == "IMAGE" or asset.state != "READY":
        return False
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before adding an asset to the scene")
    imported = [obj for obj in context.scene.objects if obj.get(OBJECT_ASSET_KEY) == identifier
                and obj.get(OBJECT_REVISION_KEY, asset.artifact if not asset.revisions else "") == asset.artifact]
    if not imported:
        if asset.kind == "SPLAT":
            obj = gaussian_splats.import_splats(context, asset.artifact)
            source = next((item.artifact for item in board.catalog.ancestors(identifier) if item.kind == "IMAGE"), "")
            obj["remi_source_image"] = source
            imported = [obj]
        elif Path(asset.artifact).suffix.lower() == ".ply":
            from ...blender.reconstructed_mesh import import_reconstructed_mesh
            obj = import_reconstructed_mesh(context, asset.artifact)
            imported = [obj]
        else:
            before = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=asset.artifact)
            imported = [obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"]
            if not imported:
                raise ValueError("The board asset did not contain a mesh")
        for obj in imported:
            obj[OBJECT_ASSET_KEY] = identifier
            obj[OBJECT_REVISION_KEY] = asset.artifact
    for other in context.view_layer.objects:
        other.select_set(False)
    for obj in imported:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    context.view_layer.objects.active = imported[0]
    window = board_window(board)
    if window is not None:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                with context.temp_override(window=window, area=area, region=window_region(area)):
                    bpy.ops.view3d.view_selected(use_all_regions=False)
                break
    return True


def texture_drop_target(board, mx, my, *, move_only=False):
    """Only a deliberate, single splat-card drag over a mesh can trigger this."""
    drag = board.card_drag
    if (move_only or drag is None or drag.get("move_only", False)
            or not drag["moved"] or board.selected != {drag["path"]}):
        return None
    bx, by, bw, bh = board.bar_rect
    if bx <= mx <= bx+bw and by <= my <= by+bh:
        return None
    if toolbar.hit(board, mx, my) is not None:
        return None
    source = board.catalog.assets.get(drag["path"])
    if source is None or source.kind != "SPLAT" or source.state != "READY" or source.hidden:
        return None
    target = board.catalog.assets.get(board.hit(mx, my, exclude={source.id}))
    if target is None or target.kind != "MESH" or target.state != "READY" or target.hidden:
        return None
    x, y, w, h = board.card_rect(target.id)
    margin = min(20, w*0.1, h*0.1)
    if not (x+margin <= mx <= x+w-margin and y+margin <= my <= y+h-margin):
        return None
    return {"source": source.id, "target": target.id, "cursor": (mx, my)}


def texture_source(board, asset):
    """Resolve a linked source or the mesh's original splat lineage, without IO."""
    explicit = asset.links.get("texture_source")
    candidates = [board.catalog.assets.get(explicit)] if explicit else board.catalog.ancestors(asset.id)
    return next((item for item in candidates if item is not None and not item.hidden
                 and item.kind == "SPLAT" and item.state == "READY"), None)


def queue_texture(context, mesh_id, source_id=None):
    """Shared bake entry point for the explicit menu and the hidden card drop."""
    board = BOARD
    if board is None:
        raise ValueError("Open the Remi Board first")
    parent = board.catalog.assets.get(mesh_id)
    if parent is None or parent.kind != "MESH" or parent.state != "READY" or parent.hidden:
        raise ValueError("Choose a completed mesh card")
    source = board.catalog.assets.get(source_id) if source_id else texture_source(board, parent)
    if source is None or source.kind != "SPLAT" or source.state != "READY" or source.hidden:
        raise ValueError("Choose a completed 3D source card")
    if not Path(parent.artifact).is_file() or not Path(source.artifact).is_file():
        raise ValueError("The mesh or Gaussian source file is missing")
    settings = board.scene.remi_texture
    with context.temp_override(scene=board.scene):
        options = {"python": generation_options(bpy.context)["python"],
                   "output": str(board.output_directory()), "mesh_artifact": parent.artifact,
                   "gaussian_source": source.artifact, "source_asset": source.id,
                   "texture": {"texture_size": int(settings.texture_size),
                               "match_distance": float(settings.match_distance),
                               "layer_width": float(settings.layer_width),
                               "normal_smoothing": int(settings.normal_smoothing)}}
    # A second drop while this exact recipe is pending selects its existing job.
    # A completed bake can still be repeated intentionally as a new result.
    existing = next((asset for asset in board.catalog.assets.values()
                     if asset.parent == parent.id and asset.operation == "TEXTURE"
                     and asset.state in {"QUEUED", "RUNNING"} and asset.settings == options), None)
    if existing is not None:
        board.selected = {existing.id}
        with context.temp_override(scene=board.scene):
            store_selection(bpy.context, board.selected)
            save_layout(bpy.context)
        return existing
    title = parent.title or "Mesh"
    result = board.derive(parent.id, "MESH", "TEXTURE", options,
                          title if title.startswith("Textured ") else "Textured " + title)
    result.links = deepcopy(parent.links)
    result.links["texture_source"] = source.id
    board.texture_effects[result.id] = {"source": source.id, "target": parent.id,
                                       "started": time.monotonic(), "finished": None}
    with context.temp_override(scene=board.scene):
        store_selection(bpy.context, board.selected)
        save_layout(bpy.context)
    return result


def run_strip_action(context, action, path):
    board = BOARD
    if board is None:
        return
    if action == "select":
        board.raise_card(path)
        board.selected = {path}
        store_selection(context, board.selected)
        return
    if action == "add3d":
        bpy.ops.remi_board.add_asset(asset_id=path)
        return
    if action == "editmesh":
        bpy.ops.remi_board.edit_mesh(asset_id=path)
        return
    if action == "generate3d":
        start_splat(context)
        return
    if action in {"mesh", "flow"}:
        operator = bpy.ops.remi_board.create_mesh if action == "mesh" else bpy.ops.remi_board.run_flow
        operator("INVOKE_DEFAULT", asset_id=path)
        return
    asset = board.catalog.assets.get(path)
    if asset is None:
        return
    if action == "texture":
        bpy.ops.remi_board.create_textured_mesh("INVOKE_DEFAULT", asset_id=path)
        return
    if action == "unlinktexture":
        asset.links.pop("texture_source", None)
        save_layout(context)
        return
    if action == "front":
        asset.yaw = asset.elevation = 0.0
        board.invalidate_preview(path)
        save_layout(context)
        return
    if action == "cancel":
        board.runner.cancel(context, board.catalog, path)
        save_layout(context)
        return
    if action == "logs":
        if Path(asset.directory).is_dir():
            bpy.ops.wm.path_open(filepath=asset.directory)
        return
    if action == "details":
        message = board.preview_errors.get(path, asset.status)
        def draw(menu, _context):
            for line in textwrap.wrap(message, 68):
                menu.layout.label(text=line)
        context.window_manager.popup_menu(draw, title="Asset Job Details", icon="INFO")
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


def curve_vertices(start, end, width=0.8):
    bend = max(30.0, abs(end[0]-start[0])*0.5)
    points = []
    for index in range(25):
        t = index/24
        s = 1-t
        points.append((s**3*start[0] + 3*s*s*t*(start[0]+bend) + 3*s*t*t*(end[0]-bend) + t**3*end[0],
                       s**3*start[1] + 3*s*s*t*start[1] + 3*s*t*t*end[1] + t**3*end[1]))
    vertices = []
    for a, b in zip(points, points[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = max(math.hypot(dx, dy), 1e-8)
        nx, ny = -dy/length*width, dx/length*width
        vertices += [(a[0]+nx,a[1]+ny), (a[0]-nx,a[1]-ny), (b[0]+nx,b[1]+ny),
                     (b[0]+nx,b[1]+ny), (a[0]-nx,a[1]-ny), (b[0]-nx,b[1]-ny)]
    return vertices


def draw_curve(start, end, colour, width=0.8):
    if (max(start[0], end[0]) < 0 or min(start[0], end[0]) > BOARD.region[0]
            or max(start[1], end[1]) < 0 or min(start[1], end[1]) > BOARD.region[1]):
        return
    program = flat_shader()
    program.bind()
    program.uniform_float("color", colour)
    batch_for_shader(program, "TRIS", {"pos": curve_vertices(start, end, width)}).draw(program)


def draw_connections(board):
    order = set(board.order)
    texture_links = []
    for key in board.order:
        asset = board.catalog.assets.get(key)
        if asset is None or asset.parent not in order:
            continue
        px, py, pw, ph = board.card_rect(asset.parent)
        x, y, w, h = board.card_rect(key)
        selected = key in board.selected or asset.parent in board.selected
        draw_curve((px+pw+5, py+ph/2), (x-5, y+h/2),
                   (*ACCENT[:3], 0.65) if selected else (0.55,0.55,0.6,0.24))
        if asset.kind == "MESH" and asset.links.get("texture_source") in order:
            sx, sy = board.output_handle(asset.links["texture_source"])
            texture_links.append(((sx, sy), (x, y+h/2),
                                  (*ACCENT[:3], 0.9) if selected else (0.25,0.65,1.0,0.8)))
    for start, end, colour in texture_links:
        draw_curve(start, end, colour, 1.5)


def board_draw():
    board = BOARD
    if board is None:
        return
    context = bpy.context
    window = getattr(context, "window", None)
    if window is None or window.as_pointer() != board.window:
        return
    area = getattr(context, "area", None)
    if area is None or board_area(board, window) != area:
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
    draw_connections(board)

    zoom = board.zoom
    now = time.monotonic()
    pulse = 0.5 + 0.5 * math.sin(time.time() * 2.6)
    for path in board.order:
        x, y, w, h = board.card_rect(path)
        if x + w < 0 or x > width or y + h < 0 or y > height:
            continue
        asset = board.catalog.assets.get(path)
        if board.texture_drop is not None and path == board.texture_drop["source"]:
            continue  # The lifted source is a small thumbnail, not a mesh-sized occluder.
        working = asset is not None and asset.state in {"QUEUED", "RUNNING"}
        if path.startswith("pending") or working:
            if asset is not None and asset.operation == "TEXTURE":
                texture_effects.draw_working_card(board, asset, (x, y, w, h), now)
                continue
            grey = 0.13 + 0.055 * pulse
            draw_rect(x, y, w, h, (grey, grey, grey + 0.008, 1.0))
            label = asset.status if asset is not None else "Generating image…"
            draw_text(clip_text(label, 11, w-20), x+10, y+16, 11, DIM)
            if asset is not None:
                draw_rect(x, y, w, 2.0, (0.28,0.28,0.3,1.0))
                draw_rect(x, y, w*asset.progress, 2.0, ACCENT)
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
            draw_rect(x, y, w, h, (0.11, 0.11, 0.12, 1.0))
            message = (asset.status if asset and (asset.state != "READY" or asset.status.startswith("Preview unavailable")) else
                       board.preview_errors.get(path, "Preparing preview…"))
            draw_text(clip_text(message, 11, w-20), x+10, y+h/2, 11, DIM)
            draw_x, draw_y, draw_w, draw_h = x, y, w, h
        else:
            texture, image_w, image_h, premultiplied = cached
            aspect = image_w / image_h
            if aspect >= 1.0:
                draw_w = w
                draw_h = w / aspect
            else:
                draw_h = h
                draw_w = h * aspect
            draw_x = x + (w - draw_w) / 2
            draw_y = y + (h - draw_h) / 2
            if asset is not None and asset.kind != "IMAGE":
                draw_rect(x, y, w, h, (0.11, 0.11, 0.12, 1.0))
            draw_image(texture, draw_x, draw_y, draw_w, draw_h, premultiplied)
        if path in board.selected:
            thickness = max(1.5, 2.0 * zoom)
            draw_rect(draw_x, draw_y, draw_w, thickness, ACCENT)
            draw_rect(draw_x, draw_y, thickness, draw_h, ACCENT)
            draw_rect(draw_x + draw_w - thickness, draw_y, thickness, draw_h, ACCENT)
            draw_rect(draw_x, draw_y + draw_h - thickness, draw_w, thickness, ACCENT)
        if asset is not None:
            caption = {"IMAGE": "Image", "SPLAT": "3D source", "MESH": asset.title or "Mesh"}[asset.kind]
            if asset.kind == "MESH":
                caption += " · Alt-drag orbits"
            if asset.kind == "MESH" and asset.operation == "TEXTURE":
                if not caption.lower().startswith("textured"):
                    caption += " · textured"
            elif asset.kind == "MESH" and asset.links.get("texture_source") in board.catalog.assets:
                caption += " · appearance linked"
            elif asset.kind == "MESH" and asset.state == "READY":
                caption += " · drop a 3D source to texture"
            if asset.kind == "SPLAT":
                caption += " · drop onto a Mesh · Alt-drag orbits"
            draw_text(clip_text(caption, 10, w), draw_x, draw_y-15, 10, DIM)
        if asset is not None and asset.kind == "SPLAT":
            hx, hy = board.output_handle(path)
            linked = any(item.links.get("texture_source") == path for item in board.catalog.assets.values())
            draw_rect(hx-6, hy-6, 12, 12, (0.25, 0.65, 1.0, 1.0) if linked else ACCENT)
            draw_text("3D", hx-9, hy-20, 9, DIM)
        elif asset is not None and asset.kind == "MESH":
            hx, hy = board.input_handle(path)
            linked = asset.links.get("texture_source") in board.catalog.assets
            colour = (0.25, 0.65, 1.0, 1.0) if linked else (0.55, 0.55, 0.6, 1.0)
            draw_rects(((hx-6,hy-6,12,1.5), (hx-6,hy+4.5,12,1.5),
                        (hx-6,hy-6,1.5,12), (hx+4.5,hy-6,1.5,12)), colour)
        if path == board.hover or path in board.selected:
            handle = max(7.0, 9.0 * zoom)
            hx, hy = draw_x + draw_w, draw_y
            draw_rect(hx - handle / 2, hy - handle / 2, handle, handle, ACCENT)

    if board.link_drag is not None:
        source = board.catalog.assets.get(board.link_drag["source"])
        if source is not None:
            draw_curve(board.output_handle(source.id), board.link_drag["cursor"],
                       (0.25, 0.65, 1.0, 0.9), 1.5)
            draw_text("Drop on a Mesh card", board.link_drag["cursor"][0]+12,
                      board.link_drag["cursor"][1]+8, 10, (0.45, 0.75, 1.0, 1.0))
    texture_effects.draw_transfers(board, now)
    texture_effects.draw_drop(board, now)
    draw_composer(board, width)
    draw_selection_strip(board, width)
    if board.box_drag is not None:
        x0, y0 = board.box_drag["start"]
        x1, y1 = board.box_drag["end"]
        x, y, w, h = min(x0, x1), min(y0, y1), abs(x1-x0), abs(y1-y0)
        draw_rect(x, y, w, h, (*ACCENT[:3], 0.10))
        draw_rects(((x,y,w,1), (x,y+h,w,1), (x,y,1,h), (x+w,y,1,h)), (*ACCENT[:3], 0.8))
    toolbar.draw(board, now)
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
    payload = {
        "positions": {
            str(item): [round(float(pos[0]), 2), round(float(pos[1]), 2)]
            for item, pos in board.positions.items()
            if not str(item).startswith("pending")
        },
        "scales": {
            str(item): round(float(value), 4)
            for item, value in board.scales.items()
            if not str(item).startswith("pending")
        },
        "zoom": round(float(board.zoom), 4),
        "pan": [round(float(board.pan[0]), 2), round(float(board.pan[1]), 2)],
        "assets": board.catalog.documents(),
        "order": [item for item in board.order if not item.startswith("pending")],
    }
    write_layout(context, payload)


def write_layout(context, payload):
    """Persist a board document, including while its editor is unmounted."""
    context.scene[SCENE_ASSETS_KEY] = json.dumps({"gallery": layout_key(context), "assets": payload["assets"]})
    path = layout_file()
    if path is None:
        return
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
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    except (OSError, ValueError):
        pass


def start_next(context):
    """Start the next queued prompt if nothing is running."""
    board = BOARD
    if board is None or board.running or not board.queue or board.runner.job is not None:
        return
    state = context.window_manager.remi_image
    if state.busy:
        return
    prompt = board.queue[0]
    context.scene.remi_image.prompt = prompt
    if not bpy.ops.remi.generate_image.poll():
        # A mesh/texture/3D operation may temporarily own Remi while the user
        # works elsewhere. Pause the queue rather than silently discarding it.
        return
    board.queue.pop(0)
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
    """Handle input only inside this board's editor content region."""

    bl_idname = "remi_board.run"
    bl_label = "Run Board"
    bl_options = {"INTERNAL"}

    def invoke(self, context, _event):
        global BOARD
        if BOARD is not None:
            # Undo can drop a native modal handler without removing our Python
            # state. Reattach input to the existing board, never reset its queue.
            if (context.window is not None and context.scene == BOARD.scene
                    and board_area(BOARD, context.window) == context.area
                    and not board_modal_running(context.window)):
                self._board = BOARD
                context.window_manager.modal_handler_add(self)
                return {"RUNNING_MODAL"}
            self.report({"WARNING"}, "A board is already open")
            return {"CANCELLED"}
        window = context.window
        if window is None or context.area is None or context.area.type != "IMAGE_EDITOR":
            return {"CANCELLED"}
        board = Board(context)
        region = window_region(context.area)
        if region is not None:
            board.region = (region.width, region.height)
        board.load(context, gallery_entries(context))
        if not board.restored:
            board.fit()
        board.composer = context.scene.remi_image.prompt or ""
        board.caret = len(board.composer)
        board.anchor = board.caret
        BOARD = board
        self._board = board
        folder = incoming_folder(context)
        if folder is not None:
            print(f"Remi board: drag images into {folder} or press Ctrl+V to paste")
        board._handle_image = bpy.types.SpaceImageEditor.draw_handler_add(
            board_draw, (), "WINDOW", "POST_PIXEL"
        )
        if not bpy.app.timers.is_registered(board_tick):
            bpy.app.timers.register(board_tick, first_interval=0.5)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

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
        ctrl = event.ctrl or getattr(event, "oskey", False)
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
        if key in {"RET", "NUMPAD_ENTER"}:
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
        if board is None or board is not self._board:
            return {"CANCELLED"}
        area = board_area(board, context.window)
        region = window_region(area) if area is not None else None
        if region is None:
            self._release_input(board)
            return {"PASS_THROUGH"}
        # Region x/y are already window-relative; adding area.x/y double-counts
        # the offset in split editors (and steals input from the next editor).
        mx, my = event.mouse_x - region.x, event.mouse_y - region.y
        board.region = (region.width, region.height)
        inside = 4 <= mx < region.width - 4 and 4 <= my < region.height - 4
        dragging = any((board.pan_drag is not None, board.card_drag is not None,
                        board.box_drag is not None, board.resize_drag is not None,
                        board.orbit_drag is not None, board.text_drag,
                        board.link_drag is not None))
        if not inside and not dragging:
            if event.type == "LEFTMOUSE" and event.value in {"PRESS", "RELEASE"}:
                self._release_input(board)
                save_layout(context)
            elif event.type == "MIDDLEMOUSE" and event.value == "RELEASE":
                board.pan_drag = None
            board.hover = None
            return {"PASS_THROUGH"}

        # Native window/workspace/editor shortcuts must reach Blender even while
        # the prompt has focus. Timer events must reach generation operators.
        if event.type == "WINDOW_DEACTIVATE":
            self._release_input(board)
            return {"PASS_THROUGH"}
        if (event.type.startswith("TIMER")
                or event.type == "DROP"
                or ((event.ctrl or getattr(event, "oskey", False))
                    and event.type in {"SPACE", "PAGE_UP", "PAGE_DOWN", "S", "O", "N", "Q"})
                or (event.shift and event.type.startswith("F") and event.type[1:].isdigit())
                or event.type in {"F3", "F11", "F12"}):
            return {"PASS_THROUGH"}

        if toolbar.handle_event(context, board, event, mx, my, dragging):
            self._redraw(context)
            return {"RUNNING_MODAL"}

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
            if board.card_drag is not None and board.texture_drop is not None:
                drag = board.card_drag
                board.positions[drag["path"]] = list(drag["origin"])
                self._release_input(board)
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if board.link_drag is not None:
                board.link_drag = None
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if board.box_drag is not None:
                board.selected = board.box_drag["selection"]
                store_selection(context, board.selected)
            self._release_input(board)
            if board.image_focus is not None:
                board.zoom, board.pan = board.image_focus
                board.image_focus = None
                self._redraw(context)
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if (board.card_drag is not None and event.type in {"LEFT_CTRL", "RIGHT_CTRL"}
                and event.value == "PRESS"):
            board.card_drag["move_only"] = True
            board.texture_drop = None
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if (event.ctrl or getattr(event, "oskey", False)) and event.type == "V" and event.value == "PRESS" and not board.focused:
            if paste_image(context):
                gallery.invalidate()
                board.reload(context)
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            if board.link_drag is not None:
                board.link_drag["cursor"] = (mx, my)
                board.last_navigation = time.monotonic()
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if board.orbit_drag is not None:
                drag = board.orbit_drag
                asset = board.catalog.assets.get(drag["path"])
                if asset is not None:
                    asset.yaw = (drag["yaw"] - (mx-drag["start"][0])*0.45) % 360
                    asset.elevation = max(-75, min(75, drag["elevation"] + (my-drag["start"][1])*0.45))
                    board.invalidate_preview(asset.id)
                self._redraw(context)
                return {"RUNNING_MODAL"}
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
                board.last_navigation = time.monotonic()
                drag = board.resize_drag
                delta = (mx - drag["start"][0]) / max(1.0, CELL_W * board.zoom)
                board.scales[drag["path"]] = max(
                    MIN_SCALE, min(MAX_SCALE, drag["scale"] + delta)
                )
            elif board.card_drag is not None:
                drag = board.card_drag
                drag["move_only"] = drag.get("move_only", False) or event.ctrl
                board.last_navigation = time.monotonic()
                if not drag["moved"] and math.hypot(mx-drag["start"][0], my-drag["start"][1]) < 4:
                    return {"RUNNING_MODAL"}
                drag["moved"] = True
                nx = (mx - drag["dx"] - board.pan[0]) / board.zoom
                ny = (my - drag["dy"] - board.pan[1]) / board.zoom
                position = board.positions.get(drag["path"])
                if position is not None:
                    board.move_selected(drag["path"], nx - position[0], ny - position[1])
                board.texture_drop = texture_drop_target(board, mx, my, move_only=event.ctrl or not inside)
            elif board.pan_drag is not None:
                board.last_navigation = time.monotonic()
                board.pan[0] += mx - board.pan_drag[0]
                board.pan[1] += my - board.pan_drag[1]
                board.pan_drag = (mx, my)
            elif board.box_drag is not None:
                board.last_navigation = time.monotonic()
                board.update_box_selection(mx, my)
            else:
                board.hover = board.hit(mx, my)
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "MIDDLEMOUSE":
            if event.value == "PRESS":
                self._release_input(board)
                board.pan_drag = (mx, my)
                board.last_navigation = time.monotonic()
            elif event.value == "RELEASE":
                board.pan_drag = None
                save_layout(context)
            self._redraw(context)
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
            board.last_navigation = time.monotonic()
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            path = board.hit(mx, my)
            if path is not None and not path.startswith("pending:"):
                if path not in board.selected:
                    board.selected = {path}
                store_selection(context, board.selected)
                context.scene["remi_board_target"] = path
                try:
                    bpy.ops.wm.call_menu(name="REMI_BOARD_MT_image")
                except Exception:
                    pass
                self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type in {"X", "DEL"} and event.value == "PRESS" and board.selected:
            bpy.ops.remi_board.delete("INVOKE_DEFAULT")
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
            source_id = board.link_source_at(mx, my)
            if source_id is not None:
                board.raise_card(source_id)
                board.selected = {source_id}
                store_selection(context, board.selected)
                board.link_drag = {"source": source_id, "cursor": (mx, my)}
                self._redraw(context)
                return {"RUNNING_MODAL"}
            path = board.hit(mx, my)
            asset = board.catalog.assets.get(path)
            if event.alt and asset is not None and asset.kind in {"SPLAT", "MESH"} and asset.state == "READY":
                board.selected = {path}
                store_selection(context, board.selected)
                board.orbit_drag = {"path": path, "start": (mx, my), "yaw": asset.yaw, "elevation": asset.elevation}
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
                board.box_drag = {"start": (mx, my), "end": (mx, my),
                                  "selection": set(board.selected),
                                  "mode": "ADD" if event.shift else "REMOVE" if event.ctrl else "SET"}
                board.update_box_selection(mx, my)
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
                    "start": (mx, my),
                    "moved": False,
                    "origin": list(board.positions[path]),
                    "move_only": event.ctrl,
                }
            self._redraw(context)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            if board.link_drag is not None:
                source = board.catalog.assets.get(board.link_drag["source"])
                target = board.catalog.assets.get(board.hit(mx, my))
                board.link_drag = None
                if source is not None and target is not None and target.kind == "MESH":
                    if target.state != "READY":
                        self.report({"WARNING"}, "That mesh is still processing")
                    else:
                        target.links["texture_source"] = source.id
                        board.selected = {target.id}
                        store_selection(context, board.selected)
                        board.invalidate_preview(target.id)
                        save_layout(context)
                self._redraw(context)
                return {"RUNNING_MODAL"}
            drag = board.card_drag
            drop = texture_drop_target(board, mx, my, move_only=event.ctrl or not inside)
            board.texture_drop = None
            if drop is not None:
                board.positions[drag["path"]] = list(drag["origin"])
                board.card_drag = None
                board.last_click = (None, 0.0)
                try:
                    queue_texture(context, drop["target"], drop["source"])
                except (ValueError, OSError) as exc:
                    self.report({"WARNING"}, str(exc))
                self._redraw(context)
                return {"RUNNING_MODAL"}
            if drag is not None and not drag["moved"] and not event.shift:
                last_path, last_time = board.last_click
                now = time.monotonic()
                if last_path == drag["path"] and now-last_time < 0.35:
                    focus_image(board, drag["path"])
                board.last_click = (drag["path"], now)
            elif drag is not None:
                board.last_click = (None, 0.0)
            store_selection(context, board.selected)
            board.orbit_drag = None
            board.card_drag = None
            board.pan_drag = None
            board.box_drag = None
            board.resize_drag = None
            board.text_drag = False
            board.text_word_drag = False
            save_layout(context)
            self._redraw(context)
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

        return {"PASS_THROUGH"}

    @staticmethod
    def _redraw(context):
        area = board_area(BOARD) if BOARD is not None else None
        if area is not None:
            area.tag_redraw()

    @staticmethod
    def _release_input(board):
        board.focused = False
        board.pan_drag = board.card_drag = board.resize_drag = None
        board.box_drag = None
        board.orbit_drag = None
        board.link_drag = None
        board.texture_drop = None
        board.toolbar_pressed = board.toolbar_hover = ""
        board.text_drag = board.text_word_drag = False

    def _finish(self, context):
        if BOARD is getattr(self, "_board", None):
            stop_board()

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


def board_tick():
    """Keep the queue alive even when another workspace/editor has focus."""
    board = BOARD
    if board is None:
        return None
    window = board_window(board)
    if window is None:
        stop_board()
        return None
    try:
        # A window override resets Blender's scene context. Apply the source
        # scene in a nested override so an isolated edit cannot change the board.
        with bpy.context.temp_override(window=window):
            with bpy.context.temp_override(scene=board.scene, view_layer=board.view_layer):
                advance_board(bpy.context)
    except ReferenceError:
        stop_board(save=False)
        return None
    if board.orbit_drag is not None:
        return 1/120
    animated = board.texture_drop is not None or bool(board.texture_effects)
    return 1/30 if animated and board_area(board) is not None else 0.1


def advance_board(context):
    """Pulse pending cards, advance the queue, pick up finished images."""
    board = BOARD
    state = context.window_manager.remi_image
    changed = False
    now = time.monotonic()
    changed = texture_effects.update(board, now)
    if now >= board.next_service:
        board.next_service = now + 0.15
        board.ticks += 1
        if not board.navigating() and board.orbit_drag is None and board.ticks % 4 == 0 and drain_incoming(context):
            gallery.invalidate()
            board.reload(context)
            changed = True
        if board.runner.tick(context, board.catalog):
            save_layout(context)
            changed = True
        if not state.busy and board.ticks % 3 == 0:
            entries = gallery_entries(context)
            if len(entries) > board.known:
                board.reload(context)
                board.running = ""
                changed = True
            elif board.running:
                board.drop_oldest_pending()
                board.running = ""
                changed = True
            start_next(context)
    if board.orbit_drag is None:
        # Capture the final view even if the user switches editors mid-orbit.
        if board.live_asset_id:
            board.finish_live_preview()
            changed = True
    changed = board.expire_previews() or changed
    area = board_area(board)
    if area is not None:
        changed = board.prepare_preview() or changed
        if (changed or board.focused or board.running or board.runner.job is not None
                or board.texture_drop is not None or board.texture_effects or toolbar.wants_redraw(board, now)):
            area.tag_redraw()


# --------------------------------------------------------------------------
# Open operator / panel
# --------------------------------------------------------------------------


class REMI_BOARD_OT_open(Operator):
    """Open the generation board beside a viewport in this Blender window."""

    bl_idname = "remi_board.open"
    bl_label = "Open Remi Board"
    bl_description = "Open the asset board beside a viewport; preserve your original Blender workspace"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.window is not None and not bpy.app.background

    def execute(self, context):
        from .workspace import open_workspace

        try:
            open_workspace(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_delete(Operator):
    """Delete selected cards and their files; keep unselected versions."""

    bl_idname = "remi_board.delete"
    bl_label = "Delete Selected Cards"
    bl_description = "Delete the selected cards, their asset files, and cached thumbnails"
    bl_options = {"REGISTER"}

    path: StringProperty()
    delete_files: BoolProperty(default=True, options={"HIDDEN"})

    def targets(self):
        if BOARD is None:
            return []
        selected = BOARD.selected if not self.path or self.path in BOARD.selected else {self.path}
        return [identifier for identifier in BOARD.order if identifier in selected and identifier in BOARD.catalog.assets]

    def invoke(self, context, event):
        targets = self.targets()
        if not targets:
            return {"CANCELLED"}
        return context.window_manager.invoke_confirm(
            self,
            event,
            title=f"Delete {len(targets)} selected card(s)?",
            message="Deletes their asset files and thumbnails from disk. Unselected versions and original uploaded files are kept.",
            confirm_text="Delete Files and Cards" if self.delete_files else "Remove Cards",
            icon="WARNING",
        )

    def execute(self, context):
        board = BOARD
        targets = self.targets()
        if board is None or not targets:
            return {"CANCELLED"}
        shared = {str(Path(value).resolve()) for identifier, asset in board.catalog.assets.items()
                  if identifier not in targets for value in asset.files()}
        errors, kept, removed = [], set(), 0
        for identifier in targets:
            asset = board.catalog.assets[identifier]
            board.runner.cancel(context, board.catalog, identifier)
            board.unload_preview(identifier)
            board.release_texture(identifier)
            failed = False
            if self.delete_files:
                for value in asset.files():
                    path = Path(value).resolve()
                    if str(path) in shared:
                        kept.add(str(path))
                        continue
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        errors.append(str(exc))
                        failed = True
            if not failed:
                asset.hidden = True
                board.forget(identifier)
                removed += 1
        store_selection(context, board.selected)
        save_layout(context)
        area = board_area(board)
        if area is not None:
            area.tag_redraw()
        if errors:
            self.report({"WARNING"}, f"Deleted {removed} cards; some files could not be deleted: {errors[0]}")
        elif kept:
            self.report({"INFO"}, f"Deleted {removed} cards; files shared with unselected cards were kept")
        return {"FINISHED"}


class REMI_BOARD_OT_drop_images(Operator):
    bl_idname = "remi_board.drop_images"
    bl_label = "Add Images to Remi"
    bl_options = {"REGISTER"}

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
    files: CollectionProperty(type=OperatorFileListElement, options={"SKIP_SAVE"})
    drop_x: FloatProperty(options={"HIDDEN", "SKIP_SAVE"})
    drop_y: FloatProperty(options={"HIDDEN", "SKIP_SAVE"})
    at_cursor: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return BOARD is not None and context.area == board_area(BOARD, context.window)

    def invoke(self, context, event):
        region = window_region(context.area)
        self.drop_x, self.drop_y = event.mouse_x-region.x, event.mouse_y-region.y
        self.at_cursor = True
        return self.execute(context)

    def execute(self, context):
        board = BOARD
        paths = [Path(self.directory) / file.name for file in self.files]
        if not paths and self.filepath:
            paths = [Path(self.filepath)]
        imported = []
        for path in paths:
            if path.suffix.lower() in IMAGE_SUFFIXES:
                target = import_image_file(context, path)
                if target is not None:
                    imported.append(str(target.resolve()))
        if not imported:
            self.report({"WARNING"}, "No supported image files could be imported")
            return {"CANCELLED"}
        gallery.invalidate()
        board.reload(context)
        added = [asset for asset in board.catalog.assets.values() if asset.artifact in imported]
        if self.at_cursor:
            x = (self.drop_x-board.pan[0])/board.zoom-CELL_W/2
            y = (self.drop_y-board.pan[1])/board.zoom-CELL_H/2
            for index, asset in enumerate(added):
                board.positions[asset.id] = [x+(index % 3)*(CELL_W+GAP), y-(index//3)*(CELL_H+GAP)]
        board.selected = {asset.id for asset in added}
        board.focused = False
        store_selection(context, board.selected)
        save_layout(context)
        context.area.tag_redraw()
        self.report({"INFO"}, f"Added {len(added)} image(s) to Remi")
        return {"FINISHED"}


class IMAGE_FH_remi_board(FileHandler):
    bl_idname = "IMAGE_FH_remi_board"
    bl_label = "Add Images to Remi"
    bl_import_operator = "remi_board.drop_images"
    bl_file_extensions = ";".join(sorted(IMAGE_SUFFIXES))

    @classmethod
    def poll_drop(cls, context):
        return (BOARD is not None and context.area == board_area(BOARD, context.window)
                and context.region is not None and context.region.type == "WINDOW")


class REMI_BOARD_OT_add_asset(Operator):
    bl_idname = "remi_board.add_asset"
    bl_label = "Add Asset to Scene"
    bl_options = {"REGISTER", "UNDO"}
    asset_id: StringProperty()

    def execute(self, context):
        try:
            if not add_asset_to_scene(context, self.asset_id):
                return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_create_mesh(Operator):
    bl_idname = "remi_board.create_mesh"
    bl_label = "Create Mesh on Board"
    asset_id: StringProperty()

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        self.layout.label(text="Create a new mesh asset; keep the 3D source")
        self.layout.prop(context.scene.remi_generation, "mesh_quality", text="Quality")
        self.layout.label(text="Runs locally, outside the current scene", icon="INFO")

    def execute(self, context):
        if BOARD is None:
            return {"CANCELLED"}
        parent = BOARD.catalog.assets.get(self.asset_id)
        if parent is None or parent.kind != "SPLAT" or parent.state != "READY":
            return {"CANCELLED"}
        settings = context.scene.remi_generation
        BOARD.derive(parent.id, "MESH", "CREATE_MESH", {
            "python": generation_options(context)["python"],
            "output": bpy.path.abspath(settings.mesh_output), "quality": settings.mesh_quality}, "Mesh")
        save_layout(context)
        return {"FINISHED"}


class REMI_BOARD_OT_create_textured_mesh(Operator):
    bl_idname = "remi_board.create_textured_mesh"
    bl_label = "Create Textured Mesh on Board"
    bl_description = "Bake the linked Gaussian appearance into this mesh as a new textured GLB"
    asset_id: StringProperty()

    def invoke(self, context, _event):
        asset = BOARD.catalog.assets.get(self.asset_id) if BOARD is not None else None
        source = texture_source(BOARD, asset) if asset else None
        if source is None or not Path(source.artifact).is_file():
            self.report({"ERROR"}, "Link a 3D source first")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=460, confirm_text="Create Textured Mesh")

    def draw(self, context):
        asset = BOARD.catalog.assets.get(self.asset_id)
        source = texture_source(BOARD, asset)
        self.layout.label(text=f"Mesh: {asset.title or 'Mesh'}", icon="MESH_DATA")
        self.layout.label(text=f"Appearance: {source.title or '3D source'}", icon="IMAGE_DATA")
        settings = context.scene.remi_texture
        self.layout.prop(settings, "texture_size", text="Texture")
        self.layout.prop(settings, "match_distance")
        self.layout.prop(settings, "normal_smoothing")
        self.layout.label(text="The mesh and 3D cards stay unchanged", icon="INFO")

    def execute(self, context):
        try:
            queue_texture(context, self.asset_id)
        except (ValueError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_unlink_texture(Operator):
    bl_idname = "remi_board.unlink_texture"
    bl_label = "Unlink Texture Source"
    asset_id: StringProperty()

    def execute(self, context):
        asset = BOARD.catalog.assets.get(self.asset_id) if BOARD is not None else None
        if asset is None:
            return {"CANCELLED"}
        asset.links.pop("texture_source", None)
        save_layout(context)
        return {"FINISHED"}


class REMI_BOARD_OT_run_flow(Operator):
    bl_idname = "remi_board.run_flow"
    bl_label = "Run Remi Flow on Board"
    asset_id: StringProperty()

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=520, confirm_text="Run on Board")

    def draw(self, context):
        from ...app.ui.flow_controls import draw_flow_controls
        self.layout.label(text="Create a new version in an isolated local Blender worker", icon="INFO")
        draw_flow_controls(self.layout, context, include_run=False)

    def execute(self, context):
        if BOARD is None:
            return {"CANCELLED"}
        parent = BOARD.catalog.assets.get(self.asset_id)
        if parent is None or parent.kind != "MESH" or parent.state != "READY":
            return {"CANCELLED"}
        try:
            from ...app.flow import selected_document
            document = selected_document(context)
            BOARD.derive(parent.id, "MESH", "FLOW", {"flow": document,
                "output": str(BOARD.output_directory())}, document["name"])
            save_layout(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_export(Operator, ExportHelper):
    bl_idname = "remi_board.export_asset"
    bl_label = "Export Board Asset"
    filename_ext = ""
    check_extension = False
    asset_id: StringProperty()

    def invoke(self, context, event):
        asset = BOARD.catalog.assets.get(self.asset_id) if BOARD is not None else None
        if asset is None or asset.state != "READY":
            return {"CANCELLED"}
        self.filepath = Path(asset.artifact).name
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        asset = BOARD.catalog.assets.get(self.asset_id) if BOARD is not None else None
        if asset is None or not Path(asset.artifact).is_file():
            return {"CANCELLED"}
        try:
            if Path(self.filepath).resolve() != Path(asset.artifact).resolve():
                shutil.copy2(asset.artifact, self.filepath)
        except OSError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_MT_image(Menu):
    bl_idname = "REMI_BOARD_MT_image"
    bl_label = "Asset"

    def draw(self, context):
        layout = self.layout
        identifier = str(context.scene.get("remi_board_target", ""))
        asset = BOARD.catalog.assets.get(identifier) if BOARD is not None else None
        if asset is None:
            layout.label(text="No asset", icon="INFO")
            return
        if asset.state == "READY":
            if asset.kind == "IMAGE":
                op = layout.operator("remi_board.tool_action", text="Generate 3D", icon="OUTLINER_OB_POINTCLOUD")
                op.action, op.asset_id = "generate3d", identifier
                for action, title, icon in (("copy", "Copy Prompt", "COPYDOWN"), ("use", "Use Prompt", "TEXT")):
                    op = layout.operator("remi_board.tool_action", text=title, icon=icon)
                    op.action, op.asset_id = action, identifier
            if asset.kind == "MESH":
                layout.operator("remi_board.edit_mesh", icon="EDITMODE_HLT").asset_id = identifier
            if asset.kind != "IMAGE":
                layout.operator("remi_board.add_asset", icon="IMPORT").asset_id = identifier
            if asset.kind != "IMAGE":
                op = layout.operator("remi_board.tool_action", text="Reset Preview to Front", icon="AXIS_FRONT")
                op.action, op.asset_id = "front", identifier
            if asset.kind == "SPLAT":
                layout.operator("remi_board.create_mesh", icon="MESH_DATA").asset_id = identifier
            elif asset.kind == "MESH":
                layout.operator("remi_board.run_flow", icon="PLAY").asset_id = identifier
                if texture_source(BOARD, asset) is not None:
                    layout.operator("remi_board.create_textured_mesh", text="Create Textured Mesh", icon="TEXTURE").asset_id = identifier
                if asset.links.get("texture_source"):
                    layout.operator("remi_board.unlink_texture", text="Unlink 3D Texture Source", icon="X").asset_id = identifier
            layout.operator("remi_board.export_asset", text="Export", icon="EXPORT").asset_id = identifier
        if asset.state in {"QUEUED", "RUNNING"}:
            op = layout.operator("remi_board.tool_action", text="Cancel Job", icon="CANCEL")
            op.action, op.asset_id = "cancel", identifier
        if asset.directory:
            op = layout.operator("remi_board.tool_action", text="Job Files", icon="FILE_FOLDER")
            op.action, op.asset_id = "logs", identifier
        if asset.state in {"ERROR", "INTERRUPTED"} or asset.status.startswith("Preview unavailable"):
            op = layout.operator("remi_board.tool_action", text="Details", icon="INFO")
            op.action, op.asset_id = "details", identifier
        layout.separator()
        count = len(BOARD.selected) if identifier in BOARD.selected else 1
        layout.operator("remi_board.delete", text=f"Delete {count} Selected Card(s) and Files", icon="TRASH").path = identifier


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


def draw_board_header(self, context):
    if BOARD is None or board_area(BOARD, context.window) != context.area:
        return
    row = self.layout.row(align=True)
    row.label(text="Remi", icon="IMAGE_DATA")
    row.operator("remi.add_image", text="", icon="IMPORT")
    row.popover(panel="Remi_PT_ImageSettings", text="", icon="PREFERENCES")
    row.operator("screen.screen_full_area", text="", icon="FULLSCREEN_ENTER")
    self.layout.separator()


def stop_board(*, save=True):
    global BOARD
    board = BOARD
    if board is None:
        return
    board.runner.close(bpy.context, board.catalog)
    if save:
        try:
            window = board_window(board)
            if window is not None:
                with bpy.context.temp_override(window=window):
                    with bpy.context.temp_override(scene=board.scene, view_layer=board.view_layer):
                        save_layout(bpy.context)
        except (ReferenceError, RuntimeError):
            pass
    BOARD = None
    if bpy.app.timers.is_registered(board_tick):
        bpy.app.timers.unregister(board_tick)
    remove_draw_handlers(board)
    board.cleanup()


@persistent
def before_load(_unused):
    stop_board()


@persistent
def before_save(_unused):
    """Never serialize live preview scenes/cameras into a customer's .blend."""
    board = BOARD
    if board is None:
        return
    with bpy.context.temp_override(scene=board.scene, view_layer=board.view_layer):
        board.finish_live_preview()
        for identifier in list(board.loaded_previews):
            board.unload_preview(identifier)
        save_layout(bpy.context)


_undo_host = None


@persistent
def before_undo(_unused):
    global _undo_host
    board = BOARD
    _undo_host = (board.scene.session_uid, board.scene.name, board.view_layer.name) if board is not None else None
    if board is not None:
        board.cleanup()


@persistent
def after_undo(_unused):
    """Normal Blender Undo replaces Scene IDs, including the hidden board host."""
    global _undo_host
    board = BOARD
    if board is None or _undo_host is None:
        return
    scene_id, name, layer = _undo_host
    _undo_host = None
    # Names can themselves be undone (e.g. a scene rename). The native session
    # UID survives both renames and Undo's replacement of Python ID wrappers.
    scene = next((item for item in bpy.data.scenes if item.session_uid == scene_id), None)
    scene = scene or bpy.data.scenes.get(name)
    if scene is None:
        stop_board(save=False)
        return
    board.scene = scene
    board.view_layer = scene.view_layers.get(layer) or scene.view_layers[0]
    with bpy.context.temp_override(scene=scene, view_layer=board.view_layer):
        save_layout(bpy.context)
    from . import workspace
    workspace.recover_after_undo()


CLASSES = (
    REMI_BOARD_OT_run,
    REMI_BOARD_OT_open,
    REMI_BOARD_OT_create,
    REMI_BOARD_OT_delete,
    REMI_BOARD_OT_drop_images,
    REMI_BOARD_OT_add_asset,
    REMI_BOARD_OT_create_mesh,
    REMI_BOARD_OT_create_textured_mesh,
    REMI_BOARD_OT_unlink_texture,
    REMI_BOARD_OT_run_flow,
    REMI_BOARD_OT_export,
    REMI_BOARD_MT_image,
    IMAGE_FH_remi_board,
)


def register():
    global _native_drop_override
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # Prefer Remi over Blender's generic "Open Images" drop action only on our
    # canvas. Keep every other Image Editor's normal drop behavior unchanged.
    native = getattr(bpy.types, "IMAGE_FH_drop_handler", None)
    if native is not None:
        original = native.__dict__.get("poll_drop")
        original_poll = native.poll_drop
        def poll_native_drop(cls, context):
            return not IMAGE_FH_remi_board.poll_drop(context) and original_poll(context)
        replacement = classmethod(poll_native_drop)
        native.poll_drop = replacement
        _native_drop_override = (native, original, replacement)
    bpy.types.IMAGE_HT_header.prepend(draw_board_header)
    bpy.app.handlers.load_pre.append(before_load)
    bpy.app.handlers.save_pre.append(before_save)
    bpy.app.handlers.undo_pre.append(before_undo)
    bpy.app.handlers.undo_post.append(after_undo)
    bpy.app.handlers.redo_pre.append(before_undo)
    bpy.app.handlers.redo_post.append(after_undo)


def unregister():
    global _native_drop_override
    stop_board()
    if _native_drop_override is not None:
        native, original, replacement = _native_drop_override
        if native.__dict__.get("poll_drop") is replacement:
            if original is None:
                del native.poll_drop
            else:
                native.poll_drop = original
        _native_drop_override = None
    bpy.types.IMAGE_HT_header.remove(draw_board_header)
    if before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(before_load)
    if before_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(before_save)
    for handlers, callback in ((bpy.app.handlers.undo_pre, before_undo),
                               (bpy.app.handlers.undo_post, after_undo),
                               (bpy.app.handlers.redo_pre, before_undo),
                               (bpy.app.handlers.redo_post, after_undo)):
        if callback in handlers:
            handlers.remove(callback)
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
