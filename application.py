"""Application composition root shared by Remi UI and operators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .workflow.registry import FeatureRegistry


@dataclass(frozen=True)
class RemiApplication:
    session: Any
    features: FeatureRegistry


_application: RemiApplication | None = None


def configure_application(application: RemiApplication):
    global _application
    if _application is not None and _application is not application:
        raise RuntimeError("The Remi application is already configured")
    _application = application


def get_application() -> RemiApplication:
    if _application is None:
        raise RuntimeError("The Remi application has not been configured")
    return _application


def clear_application():
    global _application
    _application = None


def create_default_application() -> RemiApplication:
    """Compose built-in dependencies at the add-on boundary."""
    from .features import create_default_registry
    from .workflow.session_runtime import runtime

    features = create_default_registry()
    runtime.configure_features(features)
    return RemiApplication(session=runtime, features=features)
