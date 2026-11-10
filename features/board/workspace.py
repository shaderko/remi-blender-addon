"""On-demand native workspace; never replace the user's current layout."""

import traceback

import bpy

from . import canvas


WORKSPACE_KEY = "remi_board_workspace"
LAYOUT_KEY = "remi_board_layout"
HOST_KEY = "remi_board_host"
_request = None
_split = None


def find_workspace(board=None):
    """Native identity survives Undo/tag loss without relying on display names."""
    if board is not None:
        found = next((item for item in bpy.data.workspaces if item.session_uid == board.workspace_uid), None)
        if found is not None:
            return found
    return next((item for item in bpy.data.workspaces if item.get(WORKSPACE_KEY)), None)


def attach_board(window, area):
    target = window.workspace
    target[WORKSPACE_KEY] = target[LAYOUT_KEY] = True
    target[HOST_KEY] = str(area.spaces.active.as_pointer())
    board = canvas.BOARD
    if board is not None:
        board.workspace = target.as_pointer()
        board.workspace_uid = target.session_uid
        board.workspace_name = target.name
        board.space = area.spaces.active.as_pointer()


def recover_after_undo():
    """Repair the board's screen/space references on the next safe UI tick."""
    global _request, _split
    board = canvas.BOARD
    if board is None:
        return
    target = find_workspace(board)
    if target is None:
        return  # The header launcher can recreate a genuinely removed tab.
    target[WORKSPACE_KEY] = True
    board.workspace, board.workspace_name = target.as_pointer(), target.name
    board.workspace_uid = target.session_uid
    window = canvas.board_window(board)
    if window is not None and window.workspace == target and window.scene == board.scene:
        _split = None
        _request = (window.as_pointer(), target.as_pointer())


def open_workspace(context):
    global _request
    window = context.window
    if window is None or bpy.app.background:
        raise RuntimeError("The Remi workspace needs an interactive Blender window")
    board = canvas.BOARD
    if board is not None and board.window != window.as_pointer():
        raise RuntimeError("Remi is already open in another Blender window")
    if board is not None and board.scene != context.scene:
        if board.queue or board.running or board.runner.job is not None or any(
                asset.state == "QUEUED" for asset in board.catalog.assets.values()):
            raise RuntimeError("Finish the queued generations before opening Remi in another scene")
        canvas.stop_board()
    # Prefer the live board's workspace even if native Undo lost its UI tags.
    workspace = find_workspace(canvas.BOARD)
    if workspace is None:
        before = {item.as_pointer() for item in bpy.data.workspaces}
        bpy.ops.workspace.duplicate()
        workspace = next((item for item in bpy.data.workspaces if item.as_pointer() not in before), None)
        if workspace is None:
            raise RuntimeError("Blender could not duplicate the workspace")
        workspace.name = "Remi"
    workspace[WORKSPACE_KEY] = True
    window.workspace = workspace
    # Blender switches screens on the next UI event, not inside the operator.
    _request = (window.as_pointer(), workspace.as_pointer())


def host_area(window):
    stored = window.workspace.get(HOST_KEY, "")
    for area in window.screen.areas:
        if any(str(space.as_pointer()) == stored for space in area.spaces):
            return area
    # Space pointers do not survive saving/reloading a .blend file.
    images = [area for area in window.screen.areas if area.type == "IMAGE_EDITOR"]
    return min(images, key=lambda area: (area.x, -area.width * area.height)) if images else None


def configure_workspace(window):
    global _split
    workspace = window.workspace
    area = host_area(window)
    # Reuse the existing Image Editor when tags or space pointers were restored
    # from an older Undo state. Do not split the same workspace a second time.
    if area is not None and (workspace.get(LAYOUT_KEY) or area.type == "IMAGE_EDITOR"):
        area.type = "IMAGE_EDITOR"
        attach_board(window, area)
        return area
    screen = window.screen
    if _split is None or _split[0] != workspace.as_pointer():
        candidates = [area for area in screen.areas if area.type == "VIEW_3D"]
        source = max(candidates or list(screen.areas), key=lambda area: area.width * area.height)
        before = {area.as_pointer() for area in screen.areas}
        with bpy.context.temp_override(window=window, screen=screen, area=source):
            bpy.ops.screen.area_split(direction="VERTICAL", factor=0.56)
        created = next((area for area in screen.areas if area.as_pointer() not in before), None)
        if created is None:
            raise RuntimeError("Blender could not split the Remi workspace")
        _split = (workspace.as_pointer(), source.as_pointer(), created.as_pointer())
        # Area coordinates are stale until Blender lays out the next frame.
        return None
    areas = [area for area in screen.areas if area.as_pointer() in _split[1:]]
    _split = None
    if len(areas) != 2:
        raise RuntimeError("The Remi workspace changed while it was being arranged; open it again")
    left, right = sorted(areas, key=lambda area: area.x)
    left.type = "IMAGE_EDITOR"
    space = left.spaces.active
    space.image = None
    space.show_region_ui = False
    space.show_region_toolbar = False
    space.show_region_header = True
    space.overlay.show_overlays = False
    right.type = "VIEW_3D"
    workspace[LAYOUT_KEY] = True
    workspace[HOST_KEY] = str(space.as_pointer())
    return left


def monitor_workspace():
    """Start the canvas after screen switches, including saved Remi workspaces."""
    global _request
    try:
        windows = bpy.context.window_manager.windows
        if _request is not None:
            window_id, workspace_id = _request
            window = next((item for item in windows if item.as_pointer() == window_id), None)
            if window is None:
                _request = None
            elif window.workspace.as_pointer() == workspace_id:
                area = configure_workspace(window)
                if area is None:
                    return 0.1
                _request = None
                attach_board(window, area)
                if canvas.BOARD is None:
                    with bpy.context.temp_override(window=window, area=area, region=canvas.window_region(area)):
                        bpy.ops.remi_board.run("INVOKE_DEFAULT")
                else:
                    # Reattach a live queue if the user closed/replaced the host
                    # area or removed the old Remi tab. Do not start two modals.
                    if not canvas.board_modal_running(window):
                        with bpy.context.temp_override(window=window, area=area, region=canvas.window_region(area)):
                            bpy.ops.remi_board.run("INVOKE_DEFAULT")
        elif canvas.BOARD is None:
            for window in windows:
                if not window.workspace.get(WORKSPACE_KEY):
                    continue
                area = host_area(window)
                if area is not None and area.type == "IMAGE_EDITOR":
                    with bpy.context.temp_override(window=window, area=area, region=canvas.window_region(area)):
                        bpy.ops.remi_board.run("INVOKE_DEFAULT")
                    break
        else:
            # A live queue need not be visible, but its UI can be repaired when
            # the user returns to the board after Undo or an editor swap.
            board = canvas.BOARD
            window = canvas.board_window(board)
            target = find_workspace(board)
            if window is not None and window.workspace == target and window.scene == board.scene:
                area = host_area(window)
                if area is not None and area.type == "IMAGE_EDITOR":
                    attach_board(window, area)
                    if not canvas.board_modal_running(window):
                        with bpy.context.temp_override(window=window, area=area, region=canvas.window_region(area)):
                            bpy.ops.remi_board.run("INVOKE_DEFAULT")
    except Exception:
        _request = None
        traceback.print_exc()
    return 0.25


def draw_board_menu(self, context):
    """Native menu placement also makes the launcher discoverable in F3 search."""
    from .editing import editing_scene

    self.layout.separator()
    if editing_scene(context) is not None:
        self.layout.operator("remi_board.return_to_board", text="Back to Remi Board", icon="BACK")
    else:
        self.layout.operator("remi_board.open", text="Open Remi Board", icon="WORKSPACE")


def register():
    bpy.types.VIEW3D_MT_view.append(draw_board_menu)
    if not bpy.app.background:
        bpy.app.timers.register(monitor_workspace, first_interval=0.25, persistent=True)


def unregister():
    global _request, _split
    _request = None
    _split = None
    if bpy.app.timers.is_registered(monitor_workspace):
        bpy.app.timers.unregister(monitor_workspace)
    bpy.types.VIEW3D_MT_view.remove(draw_board_menu)
