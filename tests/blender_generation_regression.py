"""Headless source-generation registration, import, data and failure isolation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from unittest import mock

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
else:
    remi = sys.modules["remi"]

from remi.blender.gaussian_splats import import_splats, is_splat_object, native_splats_available, uses_native_splats
from remi.features.generation.operators import runtime as generation_runtime
from remi.integrations.triposplat.splat_data import save
from remi.workflow.session import runtime as session_runtime


class Layout:
    """Minimal draw contract, including the no-selected-object entry view."""
    def __init__(self):
        self.operators = []

    def column(self, **kwargs):
        return self

    box = column
    row = column

    def popover(self, **kwargs):
        pass

    def prop(self, *args, **kwargs):
        pass

    def label(self, **kwargs):
        pass

    def separator(self):
        pass

    def operator(self, name, **kwargs):
        self.operators.append(name)
        return mock.Mock()


remi.register()
from remi.features.generation import settings as generation_settings
with tempfile.TemporaryDirectory(prefix="remi-generation-config-") as directory:
    with mock.patch.object(generation_settings.bpy.utils, "user_resource", return_value=directory):
        assert generation_settings._configuration_defaults() == {}
        configuration = Path(directory) / "remi-generation.json"
        configuration.write_text('{"python": "external-python", "models": "model-folder", "output": 42, "unknown": "ignored"}')
        assert generation_settings._configuration_defaults() == {"python": "external-python", "models": "model-folder"}
        for invalid in ("{", "[]", '"not an object"'):
            configuration.write_text(invalid)
            assert generation_settings._configuration_defaults() == {}
assert "torch" not in sys.modules
assert bpy.context.scene.remi_generation.model_cache_minutes == 5
assert bpy.ops.remi.generate_splats.poll()
assert bpy.ops.remi.import_splats.poll()
assert bpy.ops.remi.download_models.poll()
assert hasattr(bpy.types, "Remi_PT_GenerationSettings")
from remi.features.generation.panel import draw_generation
layout = Layout()
bpy.context.view_layer.objects.active = None
draw_generation(layout, bpy.context)
assert "remi.generate_splats" in layout.operators
assert "remi.import_splats" in layout.operators
from remi.features.generation.panel import Remi_PT_GenerationSettings
settings_layout = Layout()
Remi_PT_GenerationSettings.draw(type("Panel", (), {"layout": settings_layout})(), bpy.context)
assert "remi.download_models" in settings_layout.operators

# Exercise download modal success/cancel and offline gating without network access.
from types import SimpleNamespace
from remi.features.generation.model_operators import Remi_OT_DownloadModels
from remi.features.generation import model_operators
download_state = SimpleNamespace(busy=False, status="", status_kind="IDLE", directory="")
download_settings = SimpleNamespace(download_url="https://models.example/release/blender/",
                                    models=tempfile.gettempdir(), python="")
manager = mock.Mock()
manager.remi_generation = download_state
download_context = SimpleNamespace(scene=SimpleNamespace(remi_generation=download_settings),
                                   window_manager=manager, window=object(), area=mock.Mock())
download_operator = SimpleNamespace(_check_access=mock.Mock(), report=mock.Mock())
download_job = mock.Mock()
download_job.directory = Path(tempfile.gettempdir())
download_job.status.return_value = {"phase": "Downloading models · 50%"}
download_job.poll.return_value = None
with mock.patch.object(model_operators.ModelDownloadJob, "start", return_value=download_job), \
     mock.patch.object(model_operators, "shutdown_generation_server"):
    assert Remi_OT_DownloadModels.execute(download_operator, download_context) == {"RUNNING_MODAL"}
    assert download_state.busy
    assert Remi_OT_DownloadModels.modal(download_operator, download_context,
                                       SimpleNamespace(type="TIMER")) == {"RUNNING_MODAL"}
    assert download_state.status == "Downloading models · 50%"
    download_job.poll.return_value = 0
    assert Remi_OT_DownloadModels.modal(download_operator, download_context,
                                       SimpleNamespace(type="TIMER")) == {"FINISHED"}
    assert not download_state.busy and generation_runtime.job is None
    assert download_state.status_kind == "SUCCESS"
    assert Remi_OT_DownloadModels.execute(download_operator, download_context) == {"RUNNING_MODAL"}
    assert Remi_OT_DownloadModels.modal(download_operator, download_context,
                                       SimpleNamespace(type="ESC", value="PRESS")) == {"CANCELLED"}
    assert not download_state.busy and generation_runtime.job is None
    assert download_state.status_kind == "CANCELLED"
with mock.patch.object(model_operators, "bpy", SimpleNamespace(app=SimpleNamespace(online_access=False))):
    try:
        Remi_OT_DownloadModels._check_access()
    except ValueError as exc:
        assert "Allow Online Access" in str(exc)
    else:
        raise AssertionError("Offline Blender must not start a download")
bpy.context.view_layer.objects.active = bpy.data.objects.get("Cube")

with tempfile.TemporaryDirectory(prefix="remi-splat-test-") as directory:
    path = Path(directory) / "splats.npz"
    arrays = {
        "positions": np.array([[0, 0, 0], [0.5, 0, 0], [0, 0.5, 0]], dtype=np.float32),
        "scales": np.full((3, 3), 0.1, dtype=np.float32),
        "rotations": np.array([[1, 0, 0, 0], [0, 1, 0, 0], [1, 0, 0, 0]], dtype=np.float32),
        "opacities": np.array([1, 0.01, 0.5], dtype=np.float32),
        "sh_dc": np.array([[1, 0, -2], [0, 1, 0], [0, 0, 1]], dtype=np.float32),
    }
    save(path, arrays)
    original = bpy.context.active_object
    before = set(bpy.data.objects)
    obj = import_splats(bpy.context, path, preview_limit=1, display="ELLIPSOID")
    assert set(bpy.data.objects) == before | {obj}
    assert is_splat_object(obj)
    assert bpy.context.active_object is obj
    assert len(obj.data.vertices) == 3 and len(obj.data.polygons) == 0
    assert obj["remi_splat_preview_count"] == 1
    values = np.empty(9, dtype=np.float32)
    obj.data.vertices.foreach_get("co", values)
    np.testing.assert_array_equal(values.reshape(3, 3), arrays["positions"])
    for name, source in (("remi_splat_scale", "scales"), ("remi_splat_sh_dc", "sh_dc")):
        values = np.empty(9, dtype=np.float32)
        obj.data.attributes[name].data.foreach_get("vector", values)
        np.testing.assert_array_equal(values.reshape(3, 3), arrays[source])
    values = np.empty(3, dtype=np.float32)
    for index, component in enumerate("wxyz"):
        obj.data.attributes[f"remi_splat_rot_{component}"].data.foreach_get("value", values)
        np.testing.assert_array_equal(values, arrays["rotations"][:, index])
    obj.data.attributes["remi_splat_opacity"].data.foreach_get("value", values)
    np.testing.assert_array_equal(values, arrays["opacities"])
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    preview = evaluated.to_mesh()
    assert len(preview.vertices) == 12, len(preview.vertices)
    assert "remi_splat_color" in preview.attributes
    evaluated.to_mesh_clear()
    assert not bpy.ops.remi.start_session.poll()
    assert not bpy.ops.remi.run_full_flow.poll()
    try:
        session_runtime.begin(bpy.context, obj)
    except RuntimeError as exc:
        assert "not polygon surfaces" in str(exc)
    else:
        raise AssertionError("Splats must not enter a mesh session")
    assert not bpy.context.window_manager.remi_session.active

    # Simulate failure after data/material allocation, not just bad input.
    counts = tuple(len(group) for group in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups))
    with mock.patch("remi.blender.gaussian_splats._preview", side_effect=RuntimeError("preview failed")):
        try:
            import_splats(bpy.context, path, display="ELLIPSOID")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Import failure expected")
    assert counts == tuple(len(group) for group in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups))
    assert bpy.context.active_object is obj

    # Real operator dispatch, including its poll and ImportHelper adapter.
    assert bpy.ops.remi.import_splats(filepath=str(path)) == {"FINISHED"}
    assert bpy.context.active_object.get("remi_splat_count") == 3
    assert uses_native_splats(bpy.context.active_object) == native_splats_available()

    if native_splats_available():
        native = import_splats(bpy.context, path, preview_limit=1, display="NATIVE")
        assert native.type == "POINTCLOUD" and native.data.type == "GAUSSIAN_SPLAT"
        assert len(native.data.points) == 3
        assert native["remi_splat_preview_count"] == 3  # Native mode never drops low-alpha points.
        assert len(native.modifiers) == 0 and len(native.data.materials) == 0
        for attribute, kind, property_name, source in (
            ("position", "FLOAT_VECTOR", "vector", arrays["positions"]),
            ("scale", "FLOAT_VECTOR", "vector", arrays["scales"]),
            ("rotation", "QUATERNION", "value", arrays["rotations"]),
            ("radiance:base", "FLOAT4", "vector", np.column_stack((arrays["sh_dc"], arrays["opacities"]))),
        ):
            assert native.data.attributes[attribute].data_type == kind
            values = np.empty(source.size, dtype=np.float32)
            native.data.attributes[attribute].data.foreach_get(property_name, values)
            np.testing.assert_array_equal(values.reshape(source.shape), source)
        assert not bpy.ops.remi.start_session.poll()
        try:
            session_runtime.begin(bpy.context, native)
        except RuntimeError as exc:
            assert "not polygon surfaces" in str(exc)
        else:
            raise AssertionError("Native Gaussian clouds must not enter mesh sessions")
        counts = (len(bpy.data.objects), len(bpy.data.pointclouds))
        with mock.patch("remi.blender.gaussian_splats._attribute", side_effect=RuntimeError("attribute failed")):
            try:
                import_splats(bpy.context, path, display="NATIVE")
            except RuntimeError:
                pass
            else:
                raise AssertionError("Native import failure expected")
        assert counts == (len(bpy.data.objects), len(bpy.data.pointclouds))
        assert bpy.context.active_object is native
    else:
        try:
            import_splats(bpy.context, path, display="NATIVE")
        except ValueError as exc:
            assert "Blender 5.3+" in str(exc)
        else:
            raise AssertionError("Native mode must not silently fall back")

    bpy.context.view_layer.objects.active = original
    obj.select_set(False)
    original.select_set(True)
    session_runtime.begin(bpy.context, original)
    assert not bpy.ops.remi.generate_splats.poll()
    assert not bpy.ops.remi.import_splats.poll()
    assert not bpy.ops.remi.download_models.poll()
    session_runtime.cancel(bpy.context)

    fake = mock.Mock()
    generation_runtime.job = fake
    assert not bpy.ops.remi.generate_splats.poll()
    from remi.features.generation import _before_load
    _before_load(None)
    fake.cancel.assert_called_once()
    assert generation_runtime.job is None

remi.unregister()
assert not hasattr(bpy.types.Scene, "remi_generation")
remi.register()
remi.unregister()
print("Remi generation regression passed")
