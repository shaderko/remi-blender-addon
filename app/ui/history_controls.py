"""Session-owned Back, Redo, Reset, Finish, and Cancel controls."""

from ...features.base import session_command


def draw_history_controls(layout, state):
    history = layout.row(align=True)
    history.enabled = not state.interactive
    back = history.row(align=True)
    back.enabled = state.can_undo and not state.busy
    session_command(back, "UNDO", "Back", "TRIA_LEFT")
    redo = history.row(align=True)
    redo.enabled = state.can_redo and not state.busy
    session_command(redo, "REDO", "Redo", "TRIA_RIGHT")
    reset = history.row(align=True)
    reset.enabled = state.step_index > 0 and not state.busy
    session_command(reset, "RESET", "Start", "FILE_REFRESH")

    finish = layout.row(align=True)
    finish.enabled = not state.busy and not state.interactive
    finish.scale_y = 1.3
    session_command(finish, "FINISH", "Finish", "CHECKMARK")
    session_command(finish, "CANCEL", "Cancel Session", "X")
