"""Compatibility facade for selection tools moved to their own package."""

from .features.edit_tools import register, unregister
from .features.edit_tools.bridge import (
    Remi_OT_BridgeBase,
    Remi_OT_DetectBridge,
    Remi_OT_SelectSplitPart,
    Remi_OT_SmartSelectObject,
    Remi_OT_SplitByBridge,
)
from .features.edit_tools.shell import (
    Remi_OT_DoubleShellBase,
    Remi_OT_RemoveInnerShell,
    Remi_OT_SelectInnerShell,
)

__all__ = (
    "Remi_OT_BridgeBase",
    "Remi_OT_DetectBridge",
    "Remi_OT_SelectSplitPart",
    "Remi_OT_SmartSelectObject",
    "Remi_OT_SplitByBridge",
    "Remi_OT_DoubleShellBase",
    "Remi_OT_RemoveInnerShell",
    "Remi_OT_SelectInnerShell",
    "register",
    "unregister",
)
