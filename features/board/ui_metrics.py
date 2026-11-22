"""Blender already combines OS DPI and its interface scale in system.ui_scale."""
import math

import bpy


def ui_scale(context=None):
    context = context or bpy.context
    value = float(context.preferences.system.ui_scale)
    return value if math.isfinite(value) and value > 0 else 1.0


def logical_size(width, height, scale):
    return width/scale, height/scale


def logical_pointer(mouse_x, mouse_y, region, scale):
    return (mouse_x-region.x)/scale, (mouse_y-region.y)/scale
