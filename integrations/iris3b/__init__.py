"""Iris-3B text-to-image integration (external CUDA worker)."""

from .client import (
    IrisDownloadJob,
    IrisJob,
    MODEL_LABELS,
    missing_components,
    shutdown_iris_server,
)

__all__ = [
    "IrisDownloadJob",
    "IrisJob",
    "MODEL_LABELS",
    "missing_components",
    "shutdown_iris_server",
]
