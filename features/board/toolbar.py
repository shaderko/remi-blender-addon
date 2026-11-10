"""Fixed, transparent board action rail. Click runs; right-click edits defaults."""

from dataclasses import dataclass
import math
import time

import bpy
from bpy.props import StringProperty
from bpy.types import Operator


@dataclass(frozen=True)
class Tool:
    id: str
    title: str
    kinds: tuple = ()
    settings: bool = False


TOOLS = (
    Tool("generate3d", "Generate 3D", ("IMAGE",), True),
    Tool("mesh", "Create Mesh", ("SPLAT",), True),
    Tool("flow", "Run Remi Flow", ("MESH",), True),
    Tool("texture", "Texture Mesh", ("MESH",), True),
    Tool("editmesh", "Edit in Blender", ("MESH",)),
    Tool("add3d", "Add to Scene", ("SPLAT", "MESH")),
    Tool("import", "Add Images"),
    Tool("export", "Export Asset", ("IMAGE", "SPLAT", "MESH")),
    Tool("cancel", "Cancel Job"),
)
BY_ID = {tool.id: tool for tool in TOOLS}
LEFT_MARGIN = 72


def selected_asset(board, identifier=""):
    if not identifier:
        if len(board.selected) != 1:
            return None
        identifier = next(iter(board.selected))
    asset = board.catalog.assets.get(identifier)
    return asset if asset is not None and not asset.hidden else None


def enabled(board, tool, context=None, identifier=""):
    """Return an actionable explanation instead of changing/hiding the icon."""
    from . import canvas
    context = context or bpy.context
    asset = selected_asset(board, identifier)
    if tool.id == "import":
        return True, "Add image files to the board"
    if tool.id == "cancel":
        if asset is not None and asset.state in {"QUEUED", "RUNNING"}:
            return True, "Cancel the selected job; keep its source"
        current_image = (not board.selected or (len(board.selected) == 1 and board.pending
                         and next(iter(board.selected)) == board.pending[0]["key"]))
        if asset is None and current_image and board.running and context.window_manager.remi_image.busy:
            return True, "Cancel the current image generation"
        return False, "Select a queued or running job"
    names = {"IMAGE": "Image", "SPLAT": "3D source", "MESH": "Mesh"}
    required = " or ".join(names[kind] for kind in tool.kinds)
    if asset is None or asset.state != "READY" or asset.kind not in tool.kinds:
        return False, "Select one ready " + required + " card"
    if tool.id == "texture" and canvas.texture_source(board, asset) is None:
        return False, "Link a 3D appearance source first"
    if tool.id in {"editmesh", "add3d"} and context.mode != "OBJECT":
        return False, "Switch to Object Mode first"
    if tool.id == "editmesh":
        from .jobs import busy_elsewhere
        if busy_elsewhere(context):
            return False, "Finish the active Remi tool first"
    return True, "Uses the saved settings" if tool.settings else tool.title


def buttons(board):
    """Geometry depends on editor size only, never on selection or job state."""
    height = board.region[1]
    scale = min(1.6, max(0.8, bpy.context.preferences.system.ui_scale))
    pitch = min(44*scale, max(18, (height-52)/len(TOOLS)))
    size = min(36*scale, pitch-5)
    x, top = 14*scale, height-26*scale
    return [(tool, (x, top-size-index*pitch, size, size)) for index, tool in enumerate(TOOLS)]


def hit(board, mx, my):
    return next((tool for tool, (x, y, w, h) in buttons(board)
                 if x <= mx <= x+w and y <= my <= y+h), None)


def run(context, action, identifier=""):
    from . import canvas
    board = canvas.BOARD
    if board is None:
        raise ValueError("Open the Remi Board first")
    tool = BY_ID.get(action)
    if tool is None:
        if action not in {"copy", "use", "front", "logs", "details"}:
            raise ValueError("Unknown board action")
        canvas.run_strip_action(context, action, identifier)
        return {"FINISHED"}
    ready, reason = enabled(board, tool, context, identifier)
    if not ready:
        raise ValueError(reason)
    asset = selected_asset(board, identifier)
    if action == "import":
        return bpy.ops.remi.add_image("INVOKE_DEFAULT")
    if action == "cancel":
        if asset is None:
            return bpy.ops.remi.cancel_image()
        canvas.run_strip_action(context, "cancel", asset.id)
        return {"FINISHED"}
    if action == "generate3d":
        board.selected = {asset.id}
        if not canvas.start_splat(context):
            raise ValueError("Choose a completed image card")
        return {"FINISHED"}
    operators = {"mesh": bpy.ops.remi_board.create_mesh, "flow": bpy.ops.remi_board.run_flow,
                 "editmesh": bpy.ops.remi_board.edit_mesh, "add3d": bpy.ops.remi_board.add_asset,
                 "export": bpy.ops.remi_board.export_asset}
    if action == "texture":
        canvas.queue_texture(context, asset.id)
        return {"FINISHED"}
    mode = "INVOKE_DEFAULT" if action == "export" else "EXEC_DEFAULT"
    return operators[action](mode, asset_id=asset.id)


def handle_event(context, board, event, mx, my, dragging):
    """Capture only icon hits, not the transparent gaps or middle-button pan."""
    tool = hit(board, mx, my) if not dragging else None
    if tool is None and event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS":
        board.toolbar_hover = ""
    if event.type == "MOUSEMOVE":
        hover = tool.id if tool else ""
        if hover != board.toolbar_hover:
            board.toolbar_hover, board.toolbar_since = hover, time.monotonic()
        if tool is not None:
            board.hover = None
            return True
    if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "RELEASE" and board.toolbar_pressed:
        board.toolbar_pressed = ""
        return True
    if tool is None or event.type not in {"LEFTMOUSE", "RIGHTMOUSE"} or event.value != "PRESS":
        return False
    board.focused = board.text_drag = False
    board.toolbar_pressed = tool.id
    board.last_click = (None, 0.0)
    if event.type == "RIGHTMOUSE":
        if tool.settings:
            board.toolbar_hover = ""
            bpy.ops.remi_board.tool_settings("INVOKE_DEFAULT", tool=tool.id)
    elif enabled(board, tool, context)[0]:
        bpy.ops.remi_board.tool_action(action=tool.id)
    return True


def wants_redraw(board, now):
    return bool(board.toolbar_hover and now-board.toolbar_since < 0.8)


# Small, resolution-independent line icons. They need no texture decoding and
# leave the canvas visible behind the rail; only their strokes get a soft halo.
def _paths(action):
    cube = [[(4, 8), (12, 3), (20, 8), (20, 17), (12, 22), (4, 17), (4, 8)],
            [(4, 8), (12, 13), (20, 8)], [(12, 13), (12, 22)]]
    if action == "generate3d":
        return cube + [[(2, 20), (2, 23)], [(0.5, 21.5), (3.5, 21.5)], [(19, 1), (22, 1)]]
    if action == "mesh":
        return cube + [[(4, 17), (12, 13), (20, 17)], [(12, 3), (12, 13)]]
    if action == "flow":
        return [[(4, 16), (4, 20), (8, 20), (8, 16), (4, 16)],
                [(16, 4), (16, 8), (20, 8), (20, 4), (16, 4)],
                [(8, 18), (17, 18), (17, 12), (6, 12), (6, 6), (16, 6)]]
    if action == "texture":
        circle = [(12+9*math.cos(i*math.tau/32), 12+9*math.sin(i*math.tau/32)) for i in range(33)]
        return [circle, [(12, 3), (12, 21)], [(3, 12), (21, 12)], [(6, 6), (12, 12), (18, 18)]]
    if action == "editmesh":
        return [[(4, 4), (5, 10), (17, 22), (22, 17), (10, 5), (4, 4)],
                [(14, 19), (19, 14)], [(5, 10), (10, 5)], [(3, 1), (21, 1)]]
    if action == "add3d":
        return [[(3, 11), (3, 3), (21, 3), (21, 11)], [(12, 23), (12, 9)], [(7, 14), (12, 9), (17, 14)]]
    if action == "import":
        return [[(3, 20), (21, 20), (21, 4), (3, 4), (3, 20)],
                [(3, 7), (9, 13), (14, 9), (17, 12), (21, 8)], [(7, 17), (9, 17)]]
    if action == "export":
        return [[(3, 8), (3, 3), (21, 3), (21, 8)], [(12, 9), (12, 22)], [(7, 17), (12, 22), (17, 17)]]
    return [[(5, 5), (5, 19), (19, 19), (19, 5), (5, 5)]]


def _strokes(paths, rect, width, color):
    from .canvas import flat_shader, batch_for_shader
    x, y, w, h = rect
    inset, factor = w*0.18, w*0.64/24
    vertices = []
    for path in paths:
        for a, b in zip(path, path[1:]):
            ax, ay = x+inset+a[0]*factor, y+inset+a[1]*factor
            bx, by = x+inset+b[0]*factor, y+inset+b[1]*factor
            length = max(math.hypot(bx-ax, by-ay), 0.001)
            nx, ny = -(by-ay)/length*width/2, (bx-ax)/length*width/2
            vertices.extend(((ax+nx, ay+ny), (ax-nx, ay-ny), (bx+nx, by+ny),
                             (bx+nx, by+ny), (ax-nx, ay-ny), (bx-nx, by-ny)))
    shader = flat_shader()
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": vertices}).draw(shader)


def draw(board, now):
    import gpu
    from .canvas import ACCENT, draw_rect, draw_text, text_width
    gpu.state.blend_set("ALPHA")
    hovered = None
    for tool, rect in buttons(board):
        active, reason = enabled(board, tool)
        hover = board.toolbar_hover == tool.id
        color = ACCENT if hover and active else (0.78, 0.80, 0.84, 1) if active else (0.38, 0.39, 0.42, 0.6)
        paths = _paths(tool.id)
        _strokes(paths, rect, 4, (0.08, 0.085, 0.095, 0.75))
        _strokes(paths, rect, 1.7, color)
        if tool.settings:
            x, y, w, h = rect
            draw_rect(x+w-5, y+3, 2, 2, color)
        if hover:
            hovered = tool, rect, active, reason
    if hovered is None or now-board.toolbar_since < 0.45:
        return
    tool, (x, y, w, h), active, reason = hovered
    lines = [tool.title, reason]
    if tool.settings:
        lines.append("Left-click: run · Right-click: settings" if active else "Right-click: settings")
    width = max(text_width(line, 11) for line in lines)+24
    xx, yy = x+w+12, max(8, min(y, board.region[1]-len(lines)*17-20))
    gpu.state.blend_set("ALPHA")
    draw_rect(xx, yy, width, len(lines)*17+16, (0.055, 0.06, 0.07, 0.97))
    for index, line in enumerate(lines):
        draw_text(line, xx+12, yy+len(lines)*17-index*17-1, 11,
                  (0.92, 0.93, 0.95, 1) if index == 0 else (0.62, 0.65, 0.69, 1))


class REMI_BOARD_OT_tool_action(Operator):
    bl_idname = "remi_board.tool_action"
    bl_label = "Board Action"
    action: StringProperty()
    asset_id: StringProperty()

    def execute(self, context):
        try:
            result = run(context, self.action, self.asset_id)
            # A nested file browser owns its own modal lifecycle, not this adapter.
            return {"CANCELLED"} if result == {"CANCELLED"} else {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class REMI_BOARD_OT_tool_settings(Operator):
    bl_idname = "remi_board.tool_settings"
    bl_label = "Board Tool Settings"
    bl_description = "Configure the defaults used by this icon; no job starts"
    tool: StringProperty()

    def invoke(self, context, _event):
        if self.tool not in BY_ID or not BY_ID[self.tool].settings:
            return {"CANCELLED"}
        return context.window_manager.invoke_popup(self, width=400)

    def draw(self, context):
        layout = self.layout
        layout.label(text=BY_ID[self.tool].title + " · Settings", icon="PREFERENCES")
        layout.use_property_split = True
        layout.use_property_decorate = False
        if self.tool == "flow":
            from ...app.ui.flow_controls import draw_flow_controls
            draw_flow_controls(layout, context, include_run=False)
        elif self.tool == "texture":
            settings = context.scene.remi_texture
            for name in ("texture_size", "match_distance", "layer_width", "normal_smoothing"):
                layout.prop(settings, name)
        else:
            settings = context.scene.remi_generation
            state = context.window_manager.remi_generation
            controls = layout.column()
            controls.enabled = not (state.busy or state.mesh_busy)
            fields = (("mesh_quality", "mesh_output", "python") if self.tool == "mesh" else
                      ("gaussians", "seed", "model_cache_minutes", "python", "models", "output"))
            for name in fields:
                controls.prop(settings, name)
            if self.tool == "generate3d":
                controls.operator("remi.download_models", text="Download 3D Model", icon="IMPORT")
            if state.busy or state.mesh_busy:
                layout.label(text="Settings unlock when the active generation finishes", icon="TIME")
        layout.separator()
        layout.label(text="Saved for the next left-click. No job starts here.", icon="INFO")

    def execute(self, _context):
        return {"FINISHED"}


CLASSES = (REMI_BOARD_OT_tool_action, REMI_BOARD_OT_tool_settings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
