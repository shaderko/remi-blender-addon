"""Compatibility imports for the former remesh Geometry Nodes module."""

from .features.remesh.geometry_nodes import (
    _set_modifier_input,
    _set_node_mute,
    apply_remi_modifier,
    ensure_remi_node_group,
)

__all__ = (
    "_set_modifier_input",
    "_set_node_mute",
    "apply_remi_modifier",
    "ensure_remi_node_group",
)
