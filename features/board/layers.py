"""Native N-panel layer navigator for the board's existing cards and groups."""

from pathlib import Path
import time

import bpy
import bpy.utils.previews
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from .organization import CAPTION, group_bounds, layer_rows, organizer, place_in_view


_previews = None
_icons = {}
ICONS = {"IMAGE": "IMAGE_DATA", "SPLAT": "OUTLINER_OB_POINTCLOUD", "MESH": "MESH_DATA",
         "GROUP": "FILE_FOLDER", "PENDING": "TIME"}


@persistent
def clear_previews(_unused=None):
    global _previews
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
    _icons.clear()


def thumbnail_icon(asset):
    """Use existing image/preview files only; never load a mesh or start a job."""
    global _previews
    source = asset.artifact if asset.kind == "IMAGE" else asset.preview
    if not source:
        return 0
    now = time.monotonic()
    cached = _icons.get(source)
    if cached is not None and now-cached[0] < 1:
        return cached[2]
    try:
        path = Path(bpy.path.abspath(source))
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if cached is not None and cached[1] == signature:
            _icons[source] = (now, signature, cached[2])
            return cached[2]
        if _previews is None:
            _previews = bpy.utils.previews.new()
        if source in _previews:
            del _previews[source]
        icon = _previews.load(source, str(path), "IMAGE").icon_id
        _icons[source] = (now, signature, icon)
        return icon
    except (OSError, RuntimeError, ValueError, KeyError):
        _icons[source] = (now, None, 0)
        return 0


def _context_board(context):
    from . import canvas
    board = canvas.BOARD
    if (board is not None and context.area is not None
            and canvas.board_area(board, context.window) == context.area):
        return board
    return None


def reveal(board, identifier, *, extend=False):
    """Select and frame a layer, without moving cards or changing scene objects."""
    from . import canvas, organizing
    model = organizer(board)
    if identifier in model.groups:
        organizing.select_group(board, identifier, toggle=extend)
    else:
        asset = board.catalog.assets.get(identifier)
        pending = {item["key"] for item in board.pending}
        if (asset is None and identifier not in pending) or (asset is not None and asset.hidden):
            raise ValueError("That board entry is no longer available")
        if identifier not in board.positions:
            place_in_view(board, identifier)
        if identifier not in board.order:
            board.order.append(identifier)
        board.group_selected.clear()
        if extend:
            board.selected.symmetric_difference_update({identifier})
        else:
            board.selected = {identifier}
    board.focused = False
    board.image_focus = None
    canvas.editor_layout(board, board.region[0])
    left, bottom = 80, max(150, board.bar_rect[1]+board.bar_rect[3]+76)
    right, top = max(left+60, board.region[0]-24), max(bottom+60, board.region[1]-28)
    limit = min(max(board.zoom, .7), 1.25)
    for _ in range(5):
        if identifier in model.groups:
            x, y, w, h = group_bounds(board)[identifier]
        else:
            x, y = board.positions[identifier]
            w = h = 300*board.scales.get(identifier, 1)
            caption = max(CAPTION, 18/board.zoom)
            y, h = y-caption, h+caption
        zoom = max(.001, min(limit, (right-left)/(w+32), (top-bottom)/(h+32)))
        board.zoom = zoom
    board.pan = [(left+right)/2-(x+w/2)*board.zoom,
                 (bottom+top)/2-(y+h/2)*board.zoom]
    board.last_navigation = time.monotonic()


class REMI_BOARD_PG_layers(PropertyGroup):
    query: StringProperty(name="Find a Layer", description="Find cards or groups by name",
                          options={"SKIP_SAVE", "TEXTEDIT_UPDATE"})


class REMI_BOARD_OT_reveal_layer(Operator):
    bl_idname = "remi_board.reveal_layer"
    bl_label = "Find Board Entry"
    bl_description = "Select and frame this entry on the board; Shift-click to extend the selection"
    target: StringProperty(options={"HIDDEN"})
    extend: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _context_board(context) is not None

    def invoke(self, context, event):
        self.extend = event.shift or event.ctrl
        return self.execute(context)

    def execute(self, context):
        from . import canvas
        board = _context_board(context)
        area = context.area
        board.region = canvas.board_logical_size(area)
        try:
            reveal(board, self.target, extend=self.extend)
        except ValueError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        canvas.store_selection(context, board.selected)
        canvas.save_layout(context)
        area.tag_redraw()
        return {"FINISHED"}


class REMI_BOARD_OT_layer_fold(Operator):
    bl_idname = "remi_board.layer_fold"
    bl_label = "Expand or Collapse Group"
    bl_description = "Show or hide the group's children in the layer list, not on the board"
    target: StringProperty(options={"HIDDEN"})
    all_groups: BoolProperty(default=False, options={"HIDDEN"})
    collapse: BoolProperty(default=False, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _context_board(context) is not None

    def execute(self, context):
        from . import canvas
        board = _context_board(context)
        if self.all_groups:
            board.layers_collapsed = set(organizer(board).groups) if self.collapse else set()
        elif self.target in organizer(board).groups:
            board.layers_collapsed.symmetric_difference_update({self.target})
        canvas.save_layout(context)
        context.area.tag_redraw()
        return {"FINISHED"}


class REMI_BOARD_OT_layer_move(Operator):
    bl_idname = "remi_board.layer_move"
    bl_label = "Move Layer to Group"
    bl_description = "Move this layer or its selected peers into a group, or outside all groups"
    target: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _context_board(context) is not None

    def execute(self, context):
        from . import canvas, organizing
        board = _context_board(context)
        try:
            organizing.select_entry(board, self.target)
        except ValueError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        board.focused = False
        canvas.store_selection(context, board.selected)
        context.area.tag_redraw()
        bpy.ops.wm.call_menu(name="REMI_BOARD_MT_move_to_group")
        return {"FINISHED"}


class REMI_BOARD_PT_layers(Panel):
    bl_label = "Layers"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Remi"

    @classmethod
    def poll(cls, context):
        return _context_board(context) is not None

    def draw(self, context):
        board = _context_board(context)
        layout = self.layout
        layout.use_property_decorate = False
        row = layout.row(align=True)
        row.prop(context.window_manager.remi_board_layers, "query", text="", icon="VIEWZOOM")
        for collapse, icon in ((False, "TRIA_DOWN"), (True, "TRIA_RIGHT")):
            op = row.operator("remi_board.layer_fold", text="", icon=icon)
            op.all_groups, op.collapse = True, collapse
        query = context.window_manager.remi_board_layers.query
        rows = layer_rows(board, query=query, collapsed=board.layers_collapsed)
        if not rows:
            layout.label(text="No matching entries" if query.strip() else "No board entries", icon="INFO")
            return
        column = layout.column(align=True)
        for entry in rows:
            row = column.row(align=True)
            row.scale_y = 1.5
            if entry.depth:
                row.separator(factor=min(entry.depth, 8)*1.5)
            if entry.kind == "GROUP":
                row.operator("remi_board.layer_fold", text="", emboss=False,
                             icon="TRIA_RIGHT" if entry.id in board.layers_collapsed and not query.strip()
                             else "TRIA_DOWN").target = entry.id
                active = entry.id in board.group_selected
                icon_value = 0
            else:
                row.label(text="", icon="BLANK1")
                active = entry.id in board.selected
                asset = board.catalog.assets.get(entry.id)
                icon_value = thumbnail_icon(asset) if asset is not None else 0
            row.operator_context = "INVOKE_REGION_WIN"
            op = row.operator("remi_board.reveal_layer", text=entry.title, depress=active,
                              icon_value=icon_value, icon="NONE" if icon_value else ICONS[entry.kind])
            op.target = entry.id
            if entry.state in {"QUEUED", "RUNNING"}:
                row.label(text="", icon="TIME")
            elif entry.state in {"ERROR", "CANCELLED", "INTERRUPTED"}:
                row.label(text="", icon="ERROR")
            action = row.row(align=True)
            action.enabled = entry.kind != "PENDING"
            action.operator("remi_board.layer_move", text="", icon="OUTLINER_COLLECTION").target = entry.id


CLASSES = (REMI_BOARD_PG_layers, REMI_BOARD_OT_reveal_layer, REMI_BOARD_OT_layer_fold,
           REMI_BOARD_OT_layer_move, REMI_BOARD_PT_layers)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.remi_board_layers = PointerProperty(type=REMI_BOARD_PG_layers)
    bpy.app.handlers.load_pre.append(clear_previews)


def unregister():
    bpy.app.handlers.load_pre.remove(clear_previews)
    clear_previews()
    del bpy.types.WindowManager.remi_board_layers
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
