"""Compatibility imports for the legacy Blender object-helper path."""

from ...blender.mesh_objects import (
    apply_modifiers,
    duplicate_object,
    local_bounds_diagonal,
    remove_mesh_object,
    world_bounds_diagonal,
)

__all__ = (
    "apply_modifiers",
    "duplicate_object",
    "local_bounds_diagonal",
    "remove_mesh_object",
    "world_bounds_diagonal",
)
