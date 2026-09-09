"""Blender entrypoint for Remi."""

bl_info = {
    "name": "Remi",
    "author": "Remi",
    "version": (1, 14, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Remi",
    "description": "Repair, optimize, retopologize, and bake meshes inside Blender",
    "category": "Object",
}


def register():
    from .registration import register as register_addon

    register_addon()


def unregister():
    from .registration import unregister as unregister_addon

    unregister_addon()
