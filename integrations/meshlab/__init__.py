"""Bundled PyMeshLab dependency and decimation client."""

from .client import (
    ensure_pymeshlab,
    pymeshlab_unavailable_message,
    run_multi_pass_decimation,
    run_quadric_decimation,
)

__all__ = (
    "ensure_pymeshlab",
    "pymeshlab_unavailable_message",
    "run_multi_pass_decimation",
    "run_quadric_decimation",
)
