"""Installable generation board and native Blender workspace."""

from . import canvas, workspace, editing, toolbar


def register():
    canvas.register()
    workspace.register()
    editing.register()
    toolbar.register()


def unregister():
    toolbar.unregister()
    editing.unregister()
    workspace.unregister()
    canvas.unregister()
