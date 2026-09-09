"""Contract regressions for Remi's injected feature architecture."""

from pathlib import Path
import ast
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
from remi.settings import compose_scene_settings
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
assert tuple(
    blender_class.bl_idname
    for feature in default_registry
    for blender_class in feature.blender_classes()
) == (
    "remi.draw_hole_patch",
    "remi.repair_holes",
    "remi.build_alpha_wrap",
    "remi.sdf_remesh",
    "remi.apply_remesh",
    "remi.decimate",
    "remi.autoremesher",
    "remi.generate_uv",
    "remi.bake_all_maps",
    "remi.bake_diffuse",
    "remi.bake_roughness",
    "remi.bake_normal",
    "remi.bake_ao",
)
scene_settings = compose_scene_settings(default_registry)
assert len(scene_settings) == 58
assert "hole_repair_method" in scene_settings
assert "voxel_size" in scene_settings
assert "ar_target_quads" in scene_settings
assert "bake_uv_profile" in scene_settings
assert "bake_texture_size" in scene_settings

service_paths = tuple((ADDON_PARENT / "remi" / "features").glob("*/service.py")) + (
    ADDON_PARENT / "remi" / "features" / "remesh" / "decimation.py",
    ADDON_PARENT / "remi" / "features" / "retopology" / "autoremesher_service.py",
)
for service_path in service_paths:
    tree = ast.parse(service_path.read_text(encoding="utf-8"), filename=str(service_path))
    forbidden = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in {"operators", "session", "session_runtime", "ui"}
    ]
    assert not forbidden, (service_path, forbidden)

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
