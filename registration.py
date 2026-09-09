"""Explicit add-on registration order and lifecycle."""

from . import edit_tools
from . import instant_meshes
from . import operators
from . import session
from . import settings
from . import ui
from .application import (
    clear_application,
    configure_application,
    create_default_application,
)


MODULES = (
    settings,
    instant_meshes,
    operators,
    session,
    ui,
    edit_tools,
)


def register():
    application = create_default_application()
    configure_application(application)
    registered = []
    try:
        for module in MODULES:
            module.register()
            registered.append(module)
    except Exception:
        for module in reversed(registered):
            module.unregister()
        application.session.configure_features(None)
        clear_application()
        raise
    print("Remi: Registered")


def unregister():
    try:
        for module in reversed(MODULES):
            module.unregister()
    finally:
        session.runtime.configure_features(None)
        clear_application()
    print("Remi: Unregistered")
