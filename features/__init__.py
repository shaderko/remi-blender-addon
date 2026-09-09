"""Explicit composition of Remi's built-in workflow features."""

from .bake.feature import BakeFeature
from .bake.service import BakeService
from .remesh.decimation import DecimationService
from .remesh.feature import RemeshFeature
from .remesh.service import RemeshService
from .repair.feature import RepairFeature
from .repair.service import RepairService
from .retopology.feature import RetopologyFeature
from .uv.feature import UVFeature
from .uv.service import UVService
from ..workflow.registry import FeatureRegistry


def create_default_registry() -> FeatureRegistry:
    """Build the ordered workflow without filesystem or import-time discovery."""
    return FeatureRegistry(
        (
            RepairFeature(RepairService()),
            RemeshFeature(RemeshService(DecimationService())),
            RetopologyFeature(),
            UVFeature(UVService()),
            BakeFeature(BakeService()),
        )
    )


__all__ = ["create_default_registry"]
