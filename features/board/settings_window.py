"""Flat, centered board settings with direct editing of shared RNA values."""
import math

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import Operator

from .settings_sections import SECTIONS, choices, settings_items, settings_locked
from .ui_metrics import ui_scale


active_window = None


def close_window():
    if active_window is not None:
        active_window.finish()


class REMI_BOARD_OT_setting_path(Operator):
    bl_idname = "remi_board.setting_path"
    bl_label = "Choose settings path"
    filepath: StringProperty(subtype="FILE_PATH")
    directory: StringProperty(subtype="DIR_PATH")
    filemode: IntProperty(default=9)
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
        self.host = active_window
        if self.host is None or self.host.path_setting is None:
            return {"CANCELLED"}
        setting = self.host.path_setting
        self.directory_only = setting.rna.subtype == "DIR_PATH"
        self.filemode = 9 if self.directory_only else 1
        self.directory = bpy.path.abspath(setting.value) if self.directory_only else ""
        self.filepath = "" if self.directory_only else bpy.path.abspath(setting.value)
        self.host.picking = True
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if active_window is self.host:
            if not settings_locked(context):
                self.host.path_setting.assign(self.directory if self.directory_only else self.filepath)
            self.host.picking = False
            self.host.area.tag_redraw()
        return {"FINISHED"}

    def cancel(self, context):
        if active_window is self.host:
            self.host.picking = False
            self.host.area.tag_redraw()


class REMI_BOARD_OT_settings(Operator):
    bl_idname = "remi_board.settings"
    bl_label = "Remi Settings"
    bl_description = "Shared generation setup and settings for every board tool"
    section: StringProperty(default="general")

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "IMAGE_EDITOR"

    def invoke(self, context, event):
        global active_window
        if self.section not in SECTIONS:
            return {"CANCELLED"}
        if active_window is not None:
            if not active_window.commit(context):
                return {"CANCELLED"}
            active_window.section = self.section
            active_window.scroll = 0
            active_window.dropdown = None
            active_window.area.tag_redraw()
            return {"CANCELLED"}
        self.area, self.window, self.manager = context.area, context.window, context.window_manager
        self.scene = context.scene
        self.scroll = 0
        self.max_scroll = 0
        self.actions = []
        self.hover = None
        self.editing = None
        self.dropdown = None
        self.dropdown_scroll = 0
        self.picking = False
        self.path_setting = None
        self.error = ""
        self.handler = bpy.types.SpaceImageEditor.draw_handler_add(self.draw_overlay, (), "WINDOW", "POST_PIXEL")
        self.timer = self.manager.event_timer_add(.15, window=self.window)
        active_window = self
        self.manager.modal_handler_add(self)
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def finish(self):
        global active_window
        if self.handler is not None:
            bpy.types.SpaceImageEditor.draw_handler_remove(self.handler, "WINDOW")
            self.handler = None
        if self.timer is not None:
            self.manager.event_timer_remove(self.timer)
            self.timer = None
        if active_window is self:
            active_window = None
        try:
            self.area.tag_redraw()
        except ReferenceError:
            pass

    def cancel(self, context):
        self.finish()

    def commit(self, context):
        if self.editing is None:
            return True
        setting = self.editing
        if settings_locked(context):
            self.editing = None
            return True
        try:
            value = self.edit_text
            if setting.rna.type in {"INT", "FLOAT"}:
                value = int(value) if setting.rna.type == "INT" else float(value)
                if not math.isfinite(value):
                    raise ValueError("Enter a finite number")
                value = max(setting.rna.hard_min, min(setting.rna.hard_max, value))
            setting.assign(value)
            self.editing = None
            self.error = ""
            return True
        except (ValueError, TypeError, OverflowError) as error:
            self.error = str(error) or "Enter a valid value"
            return False

    def edit_event(self, context, event):
        if event.value != "PRESS":
            return
        if event.type in {"RET", "NUMPAD_ENTER", "TAB"}:
            previous = self.editing
            if self.commit(context) and event.type == "TAB":
                fields = [item for item in settings_items(context, self.section)
                          if item.kind == "property" and item.rna.type in {"INT", "FLOAT", "STRING"}]
                index = next((i for i,item in enumerate(fields) if item.owner == previous.owner and item.name == previous.name), 0)
                self.activate(context, ("property", fields[(index+(-1 if event.shift else 1)) % len(fields)]))
            return
        if event.type == "ESC":
            self.editing = None
            self.error = ""
            return
        low, high = sorted((self.caret, self.anchor))
        if event.ctrl or event.oskey:
            if event.type == "A":
                self.anchor, self.caret = 0, len(self.edit_text)
            elif event.type in {"C", "X"}:
                self.manager.clipboard = self.edit_text[low:high]
                if event.type == "X":
                    self.edit_text = self.edit_text[:low]+self.edit_text[high:]
                    self.caret = self.anchor = low
            elif event.type == "V":
                text = self.manager.clipboard.replace("\n", " ")
                self.edit_text = self.edit_text[:low]+text+self.edit_text[high:]
                self.caret = self.anchor = low+len(text)
            return
        if event.type in {"LEFT_ARROW", "RIGHT_ARROW", "HOME", "END"}:
            if event.type == "HOME": self.caret = 0
            elif event.type == "END": self.caret = len(self.edit_text)
            else: self.caret = max(0, min(len(self.edit_text), self.caret+(-1 if event.type == "LEFT_ARROW" else 1)))
            if not event.shift: self.anchor = self.caret
        elif event.type in {"BACK_SPACE", "DEL"}:
            if low == high:
                if event.type == "BACK_SPACE": low = max(0, low-1)
                else: high = min(len(self.edit_text), high+1)
            self.edit_text = self.edit_text[:low]+self.edit_text[high:]
            self.caret = self.anchor = low
        elif event.unicode and event.unicode.isprintable():
            self.edit_text = self.edit_text[:low]+event.unicode+self.edit_text[high:]
            self.caret = self.anchor = low+len(event.unicode)

    def activate(self, context, action):
        kind, target = action
        if kind == "close":
            if self.commit(context): self.finish()
        elif kind == "section":
            if self.commit(context):
                self.section, self.scroll, self.dropdown = target, 0, None
        elif kind == "choice":
            setting, value = target
            if not settings_locked(context): setting.assign(value)
            self.dropdown = None
        elif not settings_locked(context):
            if not self.commit(context): return
            if kind == "property":
                if target.rna.type == "BOOLEAN": target.assign(not target.value)
                elif target.rna.type == "ENUM":
                    self.dropdown = target
                    self.dropdown_scroll = 0
                else:
                    self.editing = target
                    self.edit_text = str(target.value)
                    self.anchor, self.caret = 0, len(self.edit_text)
            elif kind == "path":
                self.path_setting = target
                bpy.ops.remi_board.setting_path("INVOKE_DEFAULT")
            elif kind == "step":
                setting, delta = target
                step = 1 if setting.rna.type == "INT" else max(.001, setting.rna.step/100)
                setting.assign(max(setting.rna.hard_min, min(setting.rna.hard_max, setting.value+delta*step)))
            elif kind == "operator":
                if target.operator == "remi.cloud_account" and context.window_manager.remi_cloud.signed_in:
                    bpy.ops.remi.cloud_account()
                elif target.operator == "remi.cloud_logout":
                    bpy.ops.remi.cloud_logout()
                elif target.operator == "remi.edit_flow_preset":
                    bpy.ops.remi.edit_flow_preset()
                    self.scroll = 0
                else:
                    category, name = target.operator.split(".")
                    try:
                        result = getattr(getattr(bpy.ops, category), name)("INVOKE_DEFAULT", **target.kwargs)
                    except RuntimeError as error:
                        # Blender raises when a child operator reports ERROR,
                        # including normal preflight failures. Keep the settings
                        # open and show the message in our existing error row.
                        self.error = str(error).removeprefix("Error: ").strip()
                        return
                    if "CANCELLED" in result:
                        self.error = "The action could not start. Check its settings and try again."
                        return
                    self.finish()

    def modal(self, context, event):
        if active_window is not self:
            return {"FINISHED"}
        if self.picking:
            return {"PASS_THROUGH"}
        if context.scene != self.scene or not any(area == self.area for area in self.window.screen.areas):
            self.finish()
            return {"CANCELLED"}
        if self.area.type != "IMAGE_EDITOR":
            self.finish()
            return {"CANCELLED"}
        region = next(r for r in self.area.regions if r.type == "WINDOW")
        mx, my = event.mouse_x-region.x, event.mouse_y-region.y
        hit = next((action for rect, action in reversed(self.actions)
                    if rect[0] <= mx <= rect[0]+rect[2] and rect[1] <= my <= rect[1]+rect[3]), None)
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if self.dropdown is not None and (hit is None or hit[0] != "choice"):
                self.dropdown = None
            elif hit:
                self.activate(context, hit)
            elif self.editing is not None:
                self.commit(context)
        elif event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            delta = -1 if event.type == "WHEELUPMOUSE" else 1
            if self.dropdown is not None:
                self.dropdown_scroll = max(0, min(max(0, len(choices(self.dropdown, context))-7), self.dropdown_scroll+delta))
            else:
                self.scroll = max(0, min(self.max_scroll, self.scroll+delta*44))
        elif self.editing is not None:
            self.edit_event(context, event)
        elif event.type == "ESC" and event.value == "PRESS":
            if self.dropdown is not None: self.dropdown = None
            else: self.finish()
        self.hover = hit if event.type == "MOUSEMOVE" else self.hover
        self.area.tag_redraw()
        return {"RUNNING_MODAL"} if active_window is self else {"FINISHED"}

    def draw_overlay(self):
        if active_window is not self or bpy.context.area != self.area or self.picking:
            return
        import gpu
        from .canvas import ACCENT, BG, DIM, TEXT, draw_rect, draw_text, text_width_pixels as text_width
        context = bpy.context
        region = context.region
        s = min(.8*ui_scale(context), (region.width-24)/740, (region.height-24)/580)
        s = max(.35, s)
        width, height = 740*s, 580*s
        left, bottom = (region.width-width)/2, (region.height-height)/2
        self.actions = []
        locked = settings_locked(context)

        def rect(x, y, w, h, color): draw_rect(left+x*s, bottom+y*s, w*s, h*s, color)
        def text(value, x, y, size=13, color=TEXT): draw_text(value, left+x*s, bottom+y*s, size*s, color)
        def fit(value, width, size=12, tail=False):
            value = str(value)
            if text_width(value, size*s) <= width*s: return value
            while value and text_width("…"+value if tail else value+"…", size*s) > width*s:
                value = value[1:] if tail else value[:-1]
            return "…"+value if tail else value+"…"
        def action(x, y, w, h, kind, target=None):
            self.actions.append(((left+x*s, bottom+y*s, w*s, h*s), (kind, target)))

        draw_rect(0, 0, region.width, region.height, (.015,.018,.025,.65))
        rect(0, 0, 740, 580, (.105,.105,.115,.98))
        title, description = SECTIONS[self.section]
        text("Settings", 28, 538, 18)
        text("Done", 676, 540, 13)
        action(650, 524, 70, 36, "close")
        text(description, 188, 493, 12, DIM)
        x, content_w = 188, 524
        for index, (key, (label, _)) in enumerate(SECTIONS.items()):
            y = 470-index*42
            if key == self.section:
                rect(16, y-11, 152, 36, (.17,.17,.18,1))
                rect(16, y-11, 2, 36, ACCENT)
            text(label, 28, y, 12, TEXT if key == self.section else DIM)
            action(16, y-11, 152, 36, "section", key)
        items = settings_items(context, self.section)
        y = 456+self.scroll
        field_x = x+content_w*.42
        field_w = content_w*.58
        dropdown_rect = None
        for setting in items:
            row_height = 28 if setting.kind in {"heading", "note"} else 44
            if 90 <= y <= 467:
                color = DIM if locked or not setting.enabled else TEXT
                if setting.kind == "heading":
                    text(setting.label, x, y, 13, TEXT)
                elif setting.kind == "note":
                    text(fit(setting.label, content_w), x, y, 12, DIM)
                elif setting.kind == "action":
                    rect(x, y-10, content_w, 32, (.18,.18,.19,1))
                    text(setting.label, x+12, y, 12, color)
                    if not locked and setting.enabled: action(x, y-10, content_w, 32, "operator", setting)
                else:
                    rna = setting.rna
                    text(fit(setting.label, field_x-x-16), x, y, 12, color)
                    is_edit = self.editing is not None and self.editing.owner == setting.owner and self.editing.name == setting.name
                    rect(field_x, y-10, field_w, 32, (.19,.19,.21,1) if is_edit else BG)
                    value = setting.value
                    if rna.type == "BOOLEAN":
                        rect(field_x+10, y-2, 12, 12, ACCENT if value else (.26,.26,.28,1))
                        if value: text("✓", field_x+11, y-1, 10, (.08,.06,.04,1))
                        text("Enabled" if value else "Disabled", field_x+34, y, 12, color)
                    elif rna.type == "ENUM":
                        value = dict(choices(setting, context)).get(value, value)
                        text(fit(value, field_w-42), field_x+10, y, 12, color)
                        text("▾", field_x+field_w-22, y, 12, DIM)
                        if self.dropdown is not None and self.dropdown.owner == setting.owner and self.dropdown.name == setting.name:
                            dropdown_rect = (field_x, y-10, field_w)
                    else:
                        value = self.edit_text if is_edit else format(value, ".6g") if rna.type == "FLOAT" else str(value)
                        path = rna.subtype in {"FILE_PATH", "DIR_PATH"}
                        padding = 38 if path else 58 if rna.type in {"INT", "FLOAT"} else 16
                        display = fit(value, field_w-padding, tail=path or is_edit)
                        if is_edit:
                            rect(field_x, y-10, field_w, 1, ACCENT)
                            start = 0
                            while start < self.caret and text_width(value[start:self.caret],12*s) > (field_w-padding-8)*s:
                                start += 1
                            display = fit(value[start:], field_w-padding)
                            low, high = sorted((self.caret,self.anchor))
                            sx = text_width(value[start:max(start,low)],12*s)/s
                            ex = min(field_w-padding,text_width(value[start:max(start,high)],12*s)/s)
                            if high > low and ex > sx:
                                rect(field_x+10+sx, y-4, ex-sx, 20, (.26,.34,.52,1))
                            cx = text_width(value[start:self.caret],12*s)/s
                            rect(field_x+10+cx, y-4, 1, 20, TEXT)
                        text(display or "Choose a path…" if path else display, field_x+10, y, 12, color)
                        if path:
                            text("…", field_x+field_w-23, y, 14, DIM)
                            if not locked: action(field_x+field_w-32, y-10, 32, 32, "path", setting)
                        elif rna.type in {"INT", "FLOAT"}:
                            text("−", field_x+field_w-45, y, 12, DIM)
                            text("+", field_x+field_w-21, y, 12, DIM)
                            if not locked:
                                action(field_x+field_w-53, y-10, 25, 32, "step", (setting,-1))
                                action(field_x+field_w-28, y-10, 28, 32, "step", (setting,1))
                    if not locked and setting.enabled:
                        # Specific path/step actions remain above the main field hit.
                        self.actions.insert(max(0,len(self.actions)-(1 if rna.subtype in {"FILE_PATH","DIR_PATH"} else 2 if rna.type in {"INT","FLOAT"} else 0)),
                                            ((left+field_x*s,bottom+(y-10)*s,field_w*s,32*s),("property",setting)))
            y -= row_height
        self.max_scroll = max(0, 456-(y-self.scroll)-370)
        self.scroll = min(self.scroll, self.max_scroll)
        if self.max_scroll:
            track = 365
            thumb = max(22, track*track/(track+self.max_scroll))
            rect(726, 100, 2, track, (.18,.18,.19,1))
            rect(726, 100+(track-thumb)*(1-self.scroll/self.max_scroll), 2, thumb, DIM)
        hint = "Changes apply to the next job."
        if self.hover is not None and self.hover[0] == "property":
            hint = self.hover[1].rna.description or hint
        text(fit(self.error or ("Settings unlock when the active job finishes." if locked else hint), 660), 28, 31, 12, DIM)
        if dropdown_rect:
            dx, dy, dw = dropdown_rect
            options = choices(self.dropdown, context)
            visible = options[self.dropdown_scroll:self.dropdown_scroll+7]
            top = min(475, max(100+len(visible)*32, dy))
            rect(dx, top-len(visible)*32, dw, len(visible)*32, (.21,.21,.23,1))
            for index, (identifier, label) in enumerate(visible):
                oy = top-(index+1)*32
                if identifier == self.dropdown.value:
                    rect(dx, oy, 2, 32, ACCENT)
                text(fit(label, dw-20), dx+10, oy+10, 12)
                action(dx, oy, dw, 32, "choice", (self.dropdown, identifier))
        gpu.state.blend_set("NONE")


CLASSES = (REMI_BOARD_OT_setting_path, REMI_BOARD_OT_settings)


def register():
    for cls in CLASSES: bpy.utils.register_class(cls)


def unregister():
    close_window()
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
