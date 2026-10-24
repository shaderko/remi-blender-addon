"""Workspace separation, minimal generation controls and hidden runtime setup."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

import bpy

ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
else:
    remi = sys.modules["remi"]

from remi.app.ui import main_panel
from remi.app.ui.workspace import active_workspace
from remi.blender.gaussian_splats import native_splats_available
from remi.features.edit_tools.panel import Remi_PT_EditToolsPanel
from remi.features.generation.panel import Remi_PT_GenerationSettings
from remi.features.generation import settings as generation_settings


class Layout:
    def __init__(self, entries=None, parent=None):
        self.entries = entries if entries is not None else []
        self.parent = parent
        self.enabled = True
        self.scale_y = 1

    def column(self, **kwargs):
        return Layout(self.entries, self)

    row = column
    box = column

    def prop(self, owner, property_name, **kwargs):
        self.entries.append(("prop", property_name, kwargs, self))

    def label(self, **kwargs):
        self.entries.append(("label", kwargs.get("text", ""), kwargs, self))

    def separator(self):
        pass

    def popover(self, **kwargs):
        self.entries.append(("popover", kwargs["panel"], kwargs, self))

    def operator(self, name, **kwargs):
        self.entries.append(("operator", name, kwargs, self))
        return SimpleNamespace()


def draw():
    layout = Layout()
    main_panel.Remi_PT_MainPanel.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout.entries


def entries_of(entries, kind):
    return [entry for entry in entries if entry[0] == kind]


remi.register()
try:
    wm = bpy.context.window_manager
    state = wm.remi_generation
    assert wm.remi_ui.workspace == "GENERATE"
    original = bpy.context.active_object
    bpy.context.view_layer.objects.active = None
    entries = draw()
    assert {entry[1] for entry in entries_of(entries, "prop")} == {"image"}
    operators = {entry[1]: entry for entry in entries_of(entries, "operator")}
    assert "remi.start_session" not in operators
    assert "remi.run_full_flow" not in operators
    assert operators["remi.generate_splats"][2]["text"] == "Generate"
    assert not operators["remi.generate_splats"][3].enabled  # No image chosen.
    assert operators["remi.generate_splats"][3].parent.scale_y == 1.6
    assert operators["remi.import_splats"][2] == {"text": "", "icon": "IMPORT"}
    gear = entries_of(entries, "popover")
    assert len(gear) == 1 and gear[0][1] == "Remi_PT_GenerationSettings"
    assert gear[0][2]["text"] == "" and gear[0][2]["icon"] == "PREFERENCES"
    assert [entry[1] for entry in entries_of(entries, "label")] == ["Image"]
    assert not Remi_PT_EditToolsPanel.poll(bpy.context)

    bpy.context.scene.remi_generation.image = "test-image.png"
    operators = {entry[1]: entry for entry in entries_of(draw(), "operator")}
    assert operators["remi.generate_splats"][3].enabled

    settings_layout = Layout()
    Remi_PT_GenerationSettings.draw(SimpleNamespace(layout=settings_layout), bpy.context)
    properties = {entry[1] for entry in entries_of(settings_layout.entries, "prop")}
    assert {"python", "models", "output", "gaussians", "seed"} <= properties
    assert ("preview_limit" in properties) != native_splats_available()
    with mock.patch.dict(generation_settings._LOCAL_DEFAULTS, {"python": "configured-python"}, clear=True):
        assert generation_settings.default_python() == "configured-python"
    with mock.patch.dict(generation_settings._LOCAL_DEFAULTS, {}, clear=True), \
         mock.patch.object(generation_settings.shutil, "which", return_value="detected-python"):
        assert generation_settings.default_python() == "detected-python"

    state.status_kind = "SUCCESS"
    state.status = "Generated 65,536 Gaussians · native Gaussian rendering"
    state.directory = str(ROOT)
    entries = draw()
    labels = [entry[1] for entry in entries_of(entries, "label")]
    assert "Model ready" in labels and state.status not in labels
    folder = next(entry for entry in entries_of(entries, "operator") if entry[1] == "wm.path_open")
    assert folder[2]["text"] == ""
    state.status_kind = "ERROR"
    state.status = "Input image not found"
    assert state.status in [entry[1] for entry in entries_of(draw(), "label")]

    state.busy = True
    state.status_kind = "PROGRESS"
    state.status = "Generating…"
    bpy.context.view_layer.objects.active = original
    wm.remi_ui.workspace = "OPTIMIZE"
    assert active_workspace(bpy.context) == "GENERATE"
    assert not bpy.ops.remi.select_workspace.poll()
    assert not bpy.ops.remi.start_session.poll()
    assert not bpy.ops.remi.run_full_flow.poll()
    from remi.workflow.session import runtime as session_runtime
    try:
        session_runtime.begin(bpy.context, original)
    except RuntimeError as exc:
        assert "cancel image generation" in str(exc)
    else:
        raise AssertionError("A mesh session must not hide an active generation")
    assert not Remi_PT_EditToolsPanel.poll(bpy.context)
    entries = draw()
    operators = {entry[1] for entry in entries_of(entries, "operator")}
    assert "remi.cancel_generation" in operators and "remi.start_session" not in operators
    state.busy = False
    state.status = ""
    state.status_kind = "IDLE"

    bpy.context.view_layer.objects.active = original
    assert bpy.ops.remi.select_workspace(workspace="OPTIMIZE") == {"FINISHED"}
    assert Remi_PT_EditToolsPanel.poll(bpy.context)
    operators = {entry[1] for entry in entries_of(draw(), "operator")}
    assert {"remi.start_session", "remi.run_full_flow"} <= operators
    assert "remi.generate_splats" not in operators and "remi.import_splats" not in operators

    wm.remi_ui.workspace = "GENERATE"
    wm.remi_session.active = True
    assert active_workspace(bpy.context) == "OPTIMIZE"
    assert not bpy.ops.remi.select_workspace.poll()
    with mock.patch.object(main_panel, "draw_session") as session:
        draw()
        session.assert_called_once()
    wm.remi_session.active = False
finally:
    bpy.context.window_manager.remi_session.active = False
    bpy.context.window_manager.remi_generation.busy = False
    remi.unregister()
assert not hasattr(bpy.types.WindowManager, "remi_ui")
print("REMI_WORKSPACE_UI_REGRESSION_OK")
