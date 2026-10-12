"""Edit-mode topology selection tools and their UI."""

import bpy

from .bridge import (
    Remi_OT_BridgeBase,
    Remi_OT_DetectBridge,
    Remi_OT_SelectSplitPart,
    Remi_OT_SmartSelectObject,
    Remi_OT_SplitByBridge,
)
from .panel import Remi_PT_EditToolsPanel
from .shell import (
    Remi_OT_DoubleShellBase,
    Remi_OT_RemoveInnerShell,
    Remi_OT_SelectInnerShell,
)


BRIDGE_CLASSES = (
    Remi_OT_DetectBridge,
    Remi_OT_SelectSplitPart,
    Remi_OT_SplitByBridge,
    Remi_OT_SmartSelectObject,
)
SHELL_CLASSES = (Remi_OT_SelectInnerShell, Remi_OT_RemoveInnerShell)

for operator_class in BRIDGE_CLASSES:
    operator_class.__annotations__ = {
        **Remi_OT_BridgeBase.__annotations__,
        **getattr(operator_class, "__annotations__", {}),
    }
for operator_class in SHELL_CLASSES:
    operator_class.__annotations__ = {
        **Remi_OT_DoubleShellBase.__annotations__,
        **getattr(operator_class, "__annotations__", {}),
    }

CLASSES = BRIDGE_CLASSES + SHELL_CLASSES + (Remi_PT_EditToolsPanel,)


def register():
    for blender_class in CLASSES:
        bpy.utils.register_class(blender_class)


def unregister():
    for blender_class in reversed(CLASSES):
        bpy.utils.unregister_class(blender_class)
