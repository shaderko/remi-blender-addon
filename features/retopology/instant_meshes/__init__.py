"""Blender-native interactive frontend for the vendored Instant Meshes core."""

from .panel import draw_panel


def register():
    from . import properties, operators, viewport

    properties.register()
    operators.register()
    viewport.register_overlay()


def unregister():
    from . import properties, operators, viewport

    viewport.unregister_overlay()
    operators.unregister()
    properties.unregister()


__all__ = ["draw_panel", "register", "unregister"]
