"""Explicit composition of Remi's built-in workflow features."""

from .bake.feature import BakeFeature
from .remesh.feature import RemeshFeature
from .repair.feature import RepairFeature
from .retopology.feature import RetopologyFeature
from .uv.feature import UVFeature
from ..workflow.registry import FeatureRegistry


def create_default_registry() -> FeatureRegistry:
    """Build the ordered workflow without filesystem or import-time discovery."""
    return FeatureRegistry(
        (
            RepairFeature(),
            RemeshFeature(),
            RetopologyFeature(),
            UVFeature(),
            BakeFeature(),
        )
    )


__all__ = ["create_default_registry"]
