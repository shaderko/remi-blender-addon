"""Explicit add-on registration order and lifecycle."""

from . import edit_tools
from . import instant_meshes
from . import operators
from . import session
from . import settings
from . import ui


MODULES = (
    settings,
    instant_meshes,
    operators,
    session,
    ui,
    edit_tools,
)


def register():
    for module in MODULES:
        module.register()
    print("Remi: Registered")


def unregister():
    for module in reversed(MODULES):
        module.unregister()
    print("Remi: Unregistered")
