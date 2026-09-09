"""Contract regressions for Remi's injected feature architecture."""

from pathlib import Path
import ast
import sys
from types import SimpleNamespace


ADDON_PARENT = Path(__file__).resolve().parents[2]
if str(ADDON_PARENT) not in sys.path:
    sys.path.insert(0, str(ADDON_PARENT))

from remi.app.application import (
    RemiApplication,
    clear_application,
    configure_application,
    get_application,
)
from remi.features import create_default_registry
from remi.features.uv.feature import UVFeature
from remi.features.settings import compose_scene_settings
from remi.workflow.contracts import (
    FeatureAction,
    FeatureDescriptor,
    FeatureExecutionContext,
)
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
assert tuple(type(feature._service).__name__ for feature in default_registry) == (
    "RepairService",
    "RemeshService",
    "RetopologyService",
    "UVService",
    "BakeService",
)
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

feature_root = ADDON_PARENT / "remi" / "features"
service_paths = tuple(feature_root.glob("*/service.py")) + (
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

canonical_paths = (
    "app/application.py",
    "app/registration.py",
    "app/ui/main_panel.py",
    "blender/session_objects.py",
    "workflow/disk.py",
    "workflow/session.py",
    "workflow/history.py",
    "workflow/state.py",
    "features/contracts.py",
    "features/repair/alpha_wrap.py",
    "features/repair/boundary.py",
    "features/repair/guided.py",
    "features/repair/manual.py",
    "features/repair/volume.py",
    "features/remesh/geometry_nodes.py",
    "features/retopology/instant_meshes/runtime.py",
    "features/uv/engine/blender_bridge.py",
    "features/bake/engine.py",
    "integrations/meshlab/worker.py",
)
for relative_path in canonical_paths:
    assert (ADDON_PARENT / "remi" / relative_path).is_file(), relative_path

legacy_paths = (
    "alpha_wrap.py",
    "autoremesher.py",
    "baking.py",
    "edit_tools.py",
    "feature_registration.py",
    "gn_setup.py",
    "infrastructure",
    "meshlab_wrapper.py",
    "operators.py",
    "session.py",
    "session_operators.py",
    "settings.py",
    "workflow/session_runtime.py",
)
for relative_path in legacy_paths:
    assert not (ADDON_PARENT / "remi" / relative_path).exists(), relative_path

for feature_path in feature_root.rglob("*.py"):
    tree = ast.parse(feature_path.read_text(encoding="utf-8"), filename=str(feature_path))
    legacy_imports = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level < 2 or not node.module:
            continue
        root_name = node.module.split(".", 1)[0]
        if root_name in {
            "alpha_wrap",
            "autoremesher",
            "baking",
            "gn_setup",
            "infrastructure",
            "meshlab_wrapper",
            "operators",
            "session",
            "session_runtime",
            "ui",
        }:
            legacy_imports.append((node.lineno, node.module))
    assert not legacy_imports, (feature_path, legacy_imports)

session_tree = ast.parse(
    (ADDON_PARENT / "remi" / "workflow" / "session.py").read_text(encoding="utf-8")
)
assert not [
    node
    for node in ast.walk(session_tree)
    if isinstance(node, ast.ImportFrom)
    and node.module
    and node.module.split(".", 1)[0] == "features"
]


class _FakeUVService:
    def __init__(self):
        self.call = None

    def generate(self, source, settings, *, candidate=None):
        self.call = (source, settings, candidate)
        return candidate, "", {"injected": True}


fake_uv = _FakeUVService()
uv_feature = UVFeature(fake_uv)
fake_settings = object()
fake_source = object()
fake_candidate = object()
uv_result = uv_feature.execute(
    uv_feature.descriptor.actions[0],
    FeatureExecutionContext(
        blender_context=SimpleNamespace(
            scene=SimpleNamespace(remi_settings=fake_settings),
        ),
        source=fake_source,
        working_copy=fake_candidate,
    ),
)
assert fake_uv.call == (fake_source, fake_settings, fake_candidate)
assert uv_result.candidate is fake_candidate
assert uv_result.report == {"injected": True}

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
