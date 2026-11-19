"""Installable generation board and native Blender workspace."""

from . import canvas, workspace, editing, toolbar, lods, organizing, layers


def register():
    canvas.register()
    workspace.register()
    editing.register()
    toolbar.register()
    lods.register()
    organizing.register()
    layers.register()


def unregister():
    layers.unregister()
    organizing.unregister()
    lods.unregister()
    toolbar.unregister()
    editing.unregister()
    workspace.unregister()
    canvas.unregister()
