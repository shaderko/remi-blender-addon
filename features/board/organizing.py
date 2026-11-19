"""Rename/group UI and frame interaction; asset data and files stay untouched."""

import math
import time

import bpy
from bpy.props import StringProperty
from bpy.types import Menu, Operator

from .organization import HEADER, clean_name, group_bounds, organizer


def _board():
    from . import canvas
    if canvas.BOARD is None:
        raise ValueError("Open the Remi Board first")
    return canvas.BOARD


def _cards(board):
    return {key for key in board.selected if key in board.catalog.assets
            and not board.catalog.assets[key].hidden}


def sync_anchors(board):
    model = organizer(board)
    for key, rect in group_bounds(board).items():
        model.groups[key].anchor = [rect[0], rect[1]]


def _save(context, board):
    from . import canvas
    canvas.store_selection(context, board.selected)
    canvas.save_layout(context)
    area = canvas.board_area(board)
    if area is not None:
        area.tag_redraw()


def rename(board, identifier, value):
    value = clean_name(value)
    model = organizer(board)
    if identifier in model.groups:
        model.groups[identifier].title = value
    elif identifier in board.catalog.assets:
        board.catalog.assets[identifier].label = value
    else:
        raise ValueError("That card or group is no longer available")


def create_group(board, title=""):
    model = organizer(board)
    group = model.create(_cards(board), board.group_selected, title)
    board.group_selected = {group.id}
    board.selected = model.members(group.id)
    sync_anchors(board)
    return group


def select_group(board, identifier, *, toggle=False):
    model = organizer(board)
    old = {card for group in board.group_selected for card in model.members(group)}
    explicit = board.selected-old if toggle else set()
    selected = board.group_selected ^ {identifier} if toggle else {identifier}
    board.group_selected = model.roots(selected)
    board.selected = explicit | {card for key in board.group_selected for card in model.members(key)
                                 if card in board.catalog.assets and not board.catalog.assets[card].hidden}


def select_entry(board, identifier):
    """Target a layer action; retain an explicit multi-selection containing it."""
    model = organizer(board)
    if identifier in model.groups:
        if identifier not in board.group_selected:
            select_group(board, identifier)
    elif identifier in board.catalog.assets and not board.catalog.assets[identifier].hidden:
        if board.group_selected or identifier not in board.selected:
            board.selected = {identifier}
        board.group_selected.clear()
    else:
        raise ValueError("That board entry is no longer available")


def drag_snapshot(board, cards, groups=()):
    """Freeze drop zones before auto-fit starts following the dragged contents."""
    model = organizer(board)
    sync_anchors(board)
    positioned = set(cards)
    cards = {key for key in cards if key in board.catalog.assets and not board.catalog.assets[key].hidden}
    groups = model.roots(groups)
    return {"cards": cards, "groups": groups, "bounds": group_bounds(board),
            "positions": {key: list(board.positions[key]) for key in positioned if key in board.positions},
            "anchors": {key: list(group.anchor) for key, group in model.groups.items()}}


def cancel_drag(board, drag):
    model = organizer(board)
    for key, position in drag.get("positions", {}).items():
        if key in board.positions:
            board.positions[key] = list(position)
    for key, anchor in drag.get("anchors", {}).items():
        if key in model.groups:
            model.groups[key].anchor = list(anchor)
    board.group_drop = None


def drop_target(board, drag, mx, my):
    """Deepest original frame under the cursor, or root on clear board space."""
    from . import toolbar
    if not drag.get("moved") or drag.get("move_only"):
        return None
    if not (4 <= mx < board.region[0]-4 and 4 <= my < board.region[1]-4):
        return None
    if toolbar.hit(board, mx, my) is not None:
        return None
    bx, by, bw, bh = board.bar_rect
    if bx <= mx <= bx+bw and by <= my <= by+bh:
        return None
    if any(x <= mx <= x+w and y <= my <= y+h for (x,y,w,h), _, _ in board.strip_hits):
        return None
    model = organizer(board)
    cards, groups = model.selection(drag.get("cards", ()), drag.get("groups", ()))
    if not cards and not groups:
        return None
    forbidden = {child for key in groups for child in model.descendants(key)}
    target = ""
    for key in sorted(drag.get("bounds", {}), key=lambda key: len(list(model.ancestors(key))), reverse=True):
        if key not in model.groups:
            continue
        x, y, w, h = screen_rect(board, drag["bounds"][key])
        if x <= mx <= x+w and y <= my <= y+h:
            if key in forbidden:
                return None
            target = key
            break
    if (all(model.membership.get(key, "") == target for key in cards)
            and all(model.groups[key].parent == target for key in groups)):
        return None
    return {"target": target, "cursor": (mx, my)}


def apply_drop(board, drag, drop):
    if drop is None:
        return False
    model = organizer(board)
    model.move_into(drag.get("cards", ()), drag.get("groups", ()), drop["target"])
    if hasattr(board, "layers_collapsed"):
        board.layers_collapsed.difference_update(model.ancestors(drop["target"]))
    return True


def screen_rect(board, rect):
    x, y, w, h = rect
    return x*board.zoom+board.pan[0], y*board.zoom+board.pan[1], w*board.zoom, h*board.zoom


def header_hit(board, mx, my):
    model = organizer(board)
    bounds = group_bounds(board)
    for key in sorted(bounds, key=lambda key: len(list(model.ancestors(key))), reverse=True):
        x, y, w, h = screen_rect(board, bounds[key])
        header = max(18, HEADER*board.zoom)
        if x <= mx <= x+w and y+h-header <= my <= y+h:
            return key
    return None


def draw(board):
    from .canvas import ACCENT, clip_text, draw_rect, draw_rects, draw_text
    model = organizer(board)
    bounds = group_bounds(board)
    selected = {child for key in board.group_selected for child in model.descendants(key)}
    for key in sorted(bounds, key=lambda key: len(list(model.ancestors(key)))):
        x, y, w, h = screen_rect(board, bounds[key])
        if x+w < 0 or y+h < 0 or x > board.region[0] or y > board.region[1]:
            continue
        active = key in selected
        drop = getattr(board, "group_drop", None)
        receiving = drop is not None and drop["target"] == key
        color = (.25, .7, 1, 1) if receiving else (*ACCENT[:3], .65) if active else (.33, .49, .59, .5)
        draw_rect(x, y, w, h, (.12, .18, .22, .45))
        header = max(18, HEADER*board.zoom)
        draw_rect(x, y+h-header, w, header, (.16, .25, .3, .88))
        draw_rects(((x,y,w,1.3), (x,y+h,w,1.3), (x,y,1.3,h), (x+w,y,1.3,h)), color)
        title = model.groups[key].title + f"  ·  {len(model.members(key))}"
        size = min(14, max(9, 13*board.zoom))
        draw_text(clip_text(title, size, w-20), x+10, y+h-header/2-size/3, size,
                  (.9, .95, 1, 1) if active else (.73, .83, .88, 1))


def draw_drop(board):
    from .canvas import clip_text, draw_rect, draw_text, text_width
    drop = getattr(board, "group_drop", None)
    if drop is None:
        return
    model = organizer(board)
    target = model.groups.get(drop["target"])
    title = "Move into " + target.title if target is not None else "Move outside all groups"
    width = min(board.region[0]-16, text_width(title, 11)+24)
    x, y = drop["cursor"]
    x, y = max(8, min(x+16, board.region[0]-width-8)), max(8, min(y+18, board.region[1]-34))
    draw_rect(x, y, width, 26, (.055, .12, .17, .97))
    draw_text(clip_text(title, 11, width-20), x+10, y+8, 11, (.55, .85, 1, 1))


def handle_event(context, board, event, mx, my):
    """Only capture group headers/shortcuts, never card bodies or native popups."""
    from . import canvas
    model = organizer(board)
    drag = getattr(board, "group_drag", None)
    if drag is not None:
        if event.type in {"LEFT_CTRL", "RIGHT_CTRL"} and event.value == "PRESS":
            drag["move_only"] = True
            board.group_drop = None
            return True
        if event.type == "ESC" and event.value == "PRESS":
            cancel_drag(board, drag)
            board.group_drag = None
            return True
        if event.type == "MOUSEMOVE":
            if not drag.get("moved") and math.hypot(mx-drag["start"][0], my-drag["start"][1]) < 4:
                return True
            drag["moved"] = True
            drag["move_only"] = drag.get("move_only", False) or event.ctrl
            dx, dy = (mx-drag["start"][0])/board.zoom, (my-drag["start"][1])/board.zoom
            for key, position in drag["positions"].items():
                board.positions[key] = [position[0]+dx, position[1]+dy]
            for key, anchor in drag["moving_anchors"].items():
                model.groups[key].anchor = [anchor[0]+dx, anchor[1]+dy]
            board.group_drop = drop_target(board, drag, mx, my)
            board.last_navigation = time.monotonic()
            return True
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            drag["move_only"] = drag.get("move_only", False) or event.ctrl
            apply_drop(board, drag, drop_target(board, drag, mx, my))
            board.group_drag = None
            board.group_drop = None
            _save(context, board)
            return True
        return True
    if any(getattr(board, name, None) is not None for name in
           ("pan_drag", "card_drag", "resize_drag", "box_drag", "orbit_drag", "link_drag")):
        return False
    ctrl = event.ctrl or getattr(event, "oskey", False)
    if event.value == "PRESS" and not board.focused:
        if event.type == "F2":
            if len(board.group_selected) == 1 or (not board.group_selected and len(_cards(board)) == 1):
                bpy.ops.remi_board.rename("INVOKE_DEFAULT")
            return True
        if ctrl and event.type == "G":
            if event.shift:
                bpy.ops.remi_board.ungroup()
            elif REMI_BOARD_OT_group.poll(context):
                bpy.ops.remi_board.group("INVOKE_DEFAULT")
            return True
        if event.type in {"X", "DEL"} and board.group_selected:
            bpy.ops.remi_board.ungroup()  # Frames only. Never delete a group's asset files.
            return True
    if event.type not in {"LEFTMOUSE", "RIGHTMOUSE"} or event.value != "PRESS":
        return False
    bx, by, bw, bh = board.bar_rect
    if bx <= mx <= bx+bw and by <= my <= by+bh:
        return False
    if any(x <= mx <= x+w and y <= my <= y+h for (x,y,w,h), _, _ in board.strip_hits):
        return False
    key = header_hit(board, mx, my)
    if key is not None:
        board.focused = False
        if not (event.type == "RIGHTMOUSE" and key in board.group_selected):
            select_group(board, key, toggle=event.shift)
        canvas.store_selection(context, board.selected)
        if event.type == "RIGHTMOUSE":
            context.scene["remi_board_group_target"] = key
            bpy.ops.wm.call_menu(name="REMI_BOARD_MT_group")
        elif key in board.group_selected:
            subtree = {child for group in board.group_selected for child in model.descendants(group)}
            board.group_drag = {**drag_snapshot(board, board.selected, board.group_selected),
                "start": (mx, my), "moved": False, "move_only": event.ctrl,
                "moving_anchors": {group: list(model.groups[group].anchor) for group in subtree}}
        return True
    if event.type == "RIGHTMOUSE" and board.hit(mx, my) is None:
        bpy.ops.wm.call_menu(name="REMI_BOARD_MT_organize")
        return True
    # Ordinary card/empty-space clicks leave group-header selection mode.
    board.group_selected.clear()
    return False


class REMI_BOARD_OT_rename(Operator):
    bl_idname = "remi_board.rename"
    bl_label = "Rename Board Entry"
    bl_options = {"REGISTER"}
    target: StringProperty(options={"HIDDEN"})
    name: StringProperty(name="Name", maxlen=256)

    def draw(self, context):
        self.layout.activate_init = True
        self.layout.prop(self, "name")

    @classmethod
    def poll(cls, context):
        from . import canvas
        return canvas.BOARD is not None and context.scene == canvas.BOARD.scene

    def invoke(self, context, _event):
        board = _board()
        model = organizer(board)
        if not self.target:
            if len(board.group_selected) == 1:
                self.target = next(iter(board.group_selected))
            elif len(board.selected) == 1:
                self.target = next(iter(board.selected))
        if self.target in model.groups:
            self.name = model.groups[self.target].title
        elif self.target in board.catalog.assets:
            asset = board.catalog.assets[self.target]
            self.name = asset.label or asset.title or {"IMAGE": "Image", "SPLAT": "3D source", "MESH": "Mesh"}[asset.kind]
        else:
            self.report({"ERROR"}, "Select one card or group header to rename")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=380)

    def execute(self, context):
        try:
            board = _board()
            rename(board, self.target, self.name)
            _save(context, board)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_group(Operator):
    bl_idname = "remi_board.group"
    bl_label = "Group Selected Board Entries"
    bl_options = {"REGISTER"}
    name: StringProperty(name="Group Name", maxlen=256)

    def draw(self, context):
        self.layout.activate_init = True
        self.layout.prop(self, "name")

    @classmethod
    def poll(cls, context):
        from . import canvas
        board = canvas.BOARD
        return board is not None and context.scene == board.scene and bool(board.selected or board.group_selected)

    def invoke(self, context, _event):
        self.name = organizer(_board()).unique_name()
        return context.window_manager.invoke_props_dialog(self, width=380)

    def execute(self, context):
        try:
            board = _board()
            create_group(board, self.name)
            _save(context, board)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_ungroup(Operator):
    bl_idname = "remi_board.ungroup"
    bl_label = "Ungroup (Keep All Cards and Files)"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        from . import canvas
        return canvas.BOARD is not None and context.scene == canvas.BOARD.scene

    def execute(self, context):
        board = _board()
        model = organizer(board)
        targets = board.group_selected or {model.membership[card] for card in _cards(board) if card in model.membership}
        model.ungroup(targets)
        board.group_selected.clear()
        _save(context, board)
        return {"FINISHED"}


class REMI_BOARD_OT_move_to_group(Operator):
    bl_idname = "remi_board.move_to_group"
    bl_label = "Move Selection to Group"
    bl_options = {"REGISTER"}
    group_id: StringProperty()

    @classmethod
    def poll(cls, context):
        return REMI_BOARD_OT_group.poll(context)

    def execute(self, context):
        try:
            board = _board()
            model = organizer(board)
            sync_anchors(board)
            model.move_into(_cards(board), board.group_selected, self.group_id)
            if hasattr(board, "layers_collapsed"):
                board.layers_collapsed.difference_update(model.ancestors(self.group_id))
            _save(context, board)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_MT_move_to_group(Menu):
    bl_idname = "REMI_BOARD_MT_move_to_group"
    bl_label = "Move Into Group"

    def draw(self, context):
        board = _board()
        model = organizer(board)
        self.layout.operator("remi_board.move_to_group", text="Outside All Groups").group_id = ""
        forbidden = {child for key in board.group_selected for child in model.descendants(key)}
        for key in sorted(model.groups, key=model.path):
            row = self.layout.row()
            row.enabled = key not in forbidden
            row.operator("remi_board.move_to_group", text=model.path(key)).group_id = key


def draw_actions(layout, *, target=""):
    # Menus default to EXEC_REGION_WIN. Naming needs invoke() to initialize the
    # current name and open the text dialog, just like the keyboard shortcuts.
    previous_context = layout.operator_context
    layout.operator_context = "INVOKE_REGION_WIN"
    layout.operator("remi_board.rename", text="Rename", icon="GREASEPENCIL").target = target
    layout.operator("remi_board.group", text="Group Selection", icon="OUTLINER_COLLECTION")
    layout.operator_context = previous_context
    layout.menu("REMI_BOARD_MT_move_to_group")
    layout.operator("remi_board.ungroup", text="Ungroup (Keep Contents)")


class REMI_BOARD_MT_group(Menu):
    bl_idname = "REMI_BOARD_MT_group"
    bl_label = "Board Group"

    def draw(self, context):
        draw_actions(self.layout, target=str(context.scene.get("remi_board_group_target", "")))


class REMI_BOARD_MT_organize(Menu):
    bl_idname = "REMI_BOARD_MT_organize"
    bl_label = "Organize Board"

    def draw(self, context):
        draw_actions(self.layout)


CLASSES = (REMI_BOARD_OT_rename, REMI_BOARD_OT_group, REMI_BOARD_OT_ungroup,
           REMI_BOARD_OT_move_to_group, REMI_BOARD_MT_move_to_group, REMI_BOARD_MT_group,
           REMI_BOARD_MT_organize)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
