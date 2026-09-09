"""Contract regressions for Remi's injected feature architecture."""

from pathlib import Path
import sys


ADDON_PARENT = Path(__file__).resolve().parents[2]
if str(ADDON_PARENT) not in sys.path:
    sys.path.insert(0, str(ADDON_PARENT))

from remi.application import (
    RemiApplication,
    clear_application,
    configure_application,
    get_application,
)
from remi.features import create_default_registry
from remi.workflow.contracts import FeatureAction, FeatureDescriptor
from remi.workflow.registry import FeatureRegistry


class _Feature:
    def __init__(self, feature_id, actions, next_feature=None):
        action_descriptors = tuple(
            action if isinstance(action, FeatureAction) else FeatureAction(action, action.title())
            for action in actions
        )
        self.descriptor = FeatureDescriptor(
            id=feature_id,
            name=feature_id.title(),
            icon="NONE",
            next_feature=next_feature,
            actions=action_descriptors,
        )


def _assert_rejected(features, message):
    try:
        FeatureRegistry(features)
    except ValueError as exc:
        assert message in str(exc), exc
    else:
        raise AssertionError(f"Registry unexpectedly accepted: {message}")


repair = _Feature("REPAIR", ("REPAIR",), next_feature="REMESH")
remesh = _Feature("REMESH", ("REMESH", "DECIMATE"), next_feature="REMESH")
registry = FeatureRegistry((repair, remesh))

assert registry.feature_ids == ("REPAIR", "REMESH")
assert registry.action_ids == ("REPAIR", "REMESH", "DECIMATE")
assert registry.get("REPAIR") is repair
assert registry.require_action("DECIMATE").feature is remesh
assert registry.action("MISSING") is None

default_registry = create_default_registry()
assert default_registry.feature_ids == (
    "REPAIR",
    "REMESH",
    "RETOPOLOGY",
    "UV",
    "BAKE",
)
assert default_registry.action_ids == (
    "REPAIR",
    "MANUAL_REPAIR",
    "REMESH",
    "DECIMATE",
    "INSTANT_START",
    "AUTO_RETOPO",
    "UV",
    "BAKE_ALL",
    "BAKE_DIFFUSE",
    "BAKE_ROUGHNESS",
    "BAKE_NORMAL",
    "BAKE_AO",
)
assert default_registry.require_action("REPAIR").next_feature == "REMESH"
assert default_registry.require_action("MANUAL_REPAIR").next_feature == "REPAIR"

_assert_rejected(
    (_Feature("REPAIR", ("ONE",)), _Feature("REPAIR", ("TWO",))),
    "Duplicate Remi feature ID",
)
_assert_rejected(
    (_Feature("ONE", ("RUN",)), _Feature("TWO", ("RUN",))),
    "Duplicate Remi action ID",
)
_assert_rejected(
    (_Feature("ONE", ("RUN",), next_feature="MISSING"),),
    "unknown feature",
)
_assert_rejected(
    (_Feature("ONE", (FeatureAction("RUN", "Run", next_feature="MISSING"),)),),
    "unknown feature",
)

application = RemiApplication(session=object(), features=registry)
configure_application(application)
assert get_application() is application
clear_application()

print("REMI_ARCHITECTURE_REGRESSION_OK")
