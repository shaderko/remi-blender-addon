"""Explicit add-on registration order and lifecycle."""

from . import feature_registration
from . import instant_meshes
from . import selection_tools
from . import settings
from . import ui
from .compat import registration as compatibility_registration
from .workflow import session_runtime as session
from .application import (
    clear_application,
    configure_application,
    create_default_application,
)


MODULES = (
    settings,
    instant_meshes,
    feature_registration,
    compatibility_registration,
    session,
    ui,
    selection_tools,
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
