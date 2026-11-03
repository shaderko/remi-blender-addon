"""Platform-specific native module bundled with Remi."""

from . import _remi_instant_meshes as _core

Session = _core.Session
GUIDE_API_VERSION = getattr(_core, "guide_api_version", 0)

__all__ = ["Session", "GUIDE_API_VERSION"]
