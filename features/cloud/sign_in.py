"""Centered board sign-in dialog, driven by the existing browser approval flow."""
import textwrap
import time
import webbrowser

import bpy
from bpy.types import Operator

from ...integrations.remi_cloud.client import runtime
from ..board.ui_metrics import ui_scale


active_dialog = None


def close_dialog():
    if active_dialog is not None:
        active_dialog.finish()


class Remi_OT_CloudSignIn(Operator):
    bl_idname = "remi.cloud_sign_in"
    bl_label = "Sign in to Remi Cloud"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "IMAGE_EDITOR" and context.window is not None

    def invoke(self, context, event):
        global active_dialog
        if active_dialog is not None:
            return {"CANCELLED"}
        state = context.window_manager.remi_cloud
        if not state.busy and not state.user_code:
            if bpy.ops.remi.cloud_login() == {"CANCELLED"}:
                return {"CANCELLED"}
        self.area = context.area
        self.window = context.window
        self.manager = context.window_manager
        self.opened_url = ""
        self.browser_opened = False
        self.connected_at = None
        self.hover = ""
        self.actions = {}
        self.handler = bpy.types.SpaceImageEditor.draw_handler_add(self.draw_overlay, (), "WINDOW", "POST_PIXEL")
        self.timer = self.manager.event_timer_add(0.1, window=self.window)
        active_dialog = self
        self.manager.modal_handler_add(self)
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def finish(self):
        global active_dialog
        if getattr(self, "handler", None) is not None:
            bpy.types.SpaceImageEditor.draw_handler_remove(self.handler, "WINDOW")
            self.handler = None
        if getattr(self, "timer", None) is not None:
            self.manager.event_timer_remove(self.timer)
            self.timer = None
        if active_dialog is self:
            active_dialog = None
        try:
            self.area.tag_redraw()
        except ReferenceError:
            pass

    def cancel(self, context):
        self.finish()

    def stop(self, context):
        if not context.window_manager.remi_cloud.signed_in and runtime.pending:
            runtime.stop()
            from . import tick
            tick()
        self.finish()
        return {"CANCELLED"}

    def open_browser(self):
        # Read only the SDK-validated URL from the worker, never an RNA field.
        value = runtime.status()
        url = value.get("verification_url", "")
        if not value.get("user_code") or not url:
            return
        self.opened_url = url
        try:
            self.browser_opened = webbrowser.open(url, new=2)
        except (OSError, webbrowser.Error):
            self.browser_opened = False

    def modal(self, context, event):
        if active_dialog is not self:
            return {"CANCELLED"}
        if not any(area == self.area for area in self.window.screen.areas) or self.area.type != "IMAGE_EDITOR":
            return self.stop(context)
        state = context.window_manager.remi_cloud
        if event.type == "TIMER":
            if state.signed_in:
                self.connected_at = self.connected_at or time.monotonic()
                if time.monotonic() - self.connected_at > 0.8:
                    self.finish()
                    return {"FINISHED"}
            elif state.user_code and not self.opened_url:
                self.open_browser()
            self.area.tag_redraw()
        if event.type == "ESC" and event.value == "PRESS":
            return self.stop(context)
        region = next((r for r in self.area.regions if r.type == "WINDOW"), None)
        if region is None:
            return self.stop(context)
        mx, my = event.mouse_x - region.x, event.mouse_y - region.y
        self.hover = next((key for key, (x, y, w, h) in self.actions.items()
                           if x <= mx <= x+w and y <= my <= y+h), "")
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if self.hover == "cancel":
                return self.stop(context)
            if self.hover == "browser":
                self.open_browser()
            elif self.hover == "retry":
                self.opened_url = ""
                bpy.ops.remi.cloud_login()
            elif self.hover == "copy":
                self.manager.clipboard = state.user_code
        if event.type == "MOUSEMOVE":
            self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def draw_overlay(self):
        if active_dialog is not self or bpy.context.area != self.area:
            return
        import gpu
        from ..board.canvas import ACCENT, BG, DIM, TEXT, draw_rect, draw_text, text_width_pixels as text_width

        region = bpy.context.region
        state = self.manager.remi_cloud
        scale = min(ui_scale(),
                    (region.width-32)/480, (region.height-24)/320)
        scale = max(0.4, scale)
        width, height = 480*scale, 320*scale
        left, bottom = (region.width-width)/2, (region.height-height)/2
        muted = DIM
        white = TEXT

        def centered(text, y, size=13, color=white):
            draw_text(text, left+(width-text_width(text, size*scale))/2, bottom+y*scale, size*scale, color)

        def button(key, text, x, y, w, primary=False):
            rect = (left+x*scale, bottom+y*scale, w*scale, 36*scale)
            self.actions[key] = rect
            color = ACCENT if primary else (0.20, 0.20, 0.21, 1)
            if self.hover == key:
                color = tuple(min(1, c+0.06) for c in color[:3])+(1,)
            draw_rect(*rect, color)
            draw_text(text, rect[0]+(rect[2]-text_width(text, 12*scale))/2,
                      rect[1]+12*scale, 12*scale, (0.08, 0.06, 0.04, 1) if primary else white)

        self.actions = {}
        draw_rect(0, 0, region.width, region.height, (0.015, 0.018, 0.025, 0.65))
        draw_rect(left, bottom, width, height, (0.105, 0.105, 0.115, 0.97))
        if state.signed_in:
            centered("You're connected", 216, 24)
            centered(state.email, 174, 14, muted)
            centered("Ready to create with Remi Cloud", 102, 13, muted)
        else:
            failed = runtime.status().get("state") == "error"
            centered("Sign in to Remi Cloud", 270, 22)
            if failed:
                for i, line in enumerate(textwrap.wrap(state.message, width=48)[:3]):
                    centered(line, 222-i*20, 13, muted)
                button("retry", "Try again", 72, 42, 200, True)
            else:
                centered("Approve this code in your browser to connect Blender.", 232, 13, muted)
                centered("Your default browser opens automatically.", 212, 12, muted)
                code = state.user_code or "--------"
                cell, gap, group_gap = 36, 7, 14
                total = 8*cell+6*gap+group_gap
                start = (480-total)/2
                for i, char in enumerate(code[:8]):
                    x = start+i*(cell+gap)+(group_gap-gap if i >= 4 else 0)
                    draw_rect(left+x*scale, bottom+132*scale, cell*scale, 52*scale, BG)
                    draw_text(char, left+(x+(cell-text_width(char, 25*scale)/scale)/2)*scale,
                              bottom+148*scale, 25*scale, white if state.user_code else muted)
                if state.user_code:
                    self.actions["copy"] = (left+start*scale, bottom+132*scale, total*scale, 52*scale)
                centered("Waiting for approval…" if state.user_code else "Connecting to Remi Cloud…", 102, 12, muted)
                if state.user_code:
                    button("browser", "Open browser again" if self.browser_opened else "Open browser", 72, 42, 200, True)
            button("cancel", "Cancel", 284 if failed or state.user_code else 178, 42, 124)
        gpu.state.blend_set("NONE")
