"""Blender entrypoint for Remi."""

bl_info = {
    "name": "Remi",
    "author": "Remi",
    "version": (2, 7, 0),
    "blender": (5, 3, 0),
    "location": "View3D > Sidebar > Remi > Open Remi Board",
    "description": "Generate and manage assets on the Remi Board; edit, optimize, texture, and bake meshes in Blender",
    "category": "Object",
}


def register():
    from .app.registration import register as register_addon

    register_addon()


def unregister():
    from .app.registration import unregister as unregister_addon

    unregister_addon()
