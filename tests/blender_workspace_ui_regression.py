"""Native sidebar launches the board and retains optional mesh tools."""
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
from remi.features.board import editing, workspace as board_workspace


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

    def template_icon(self, icon_value=0, scale=1.0):
        self.entries.append(("icon", icon_value, scale, self))

    def progress(self, factor=0.0, type="BAR", text=""):
        self.entries.append(("progress", factor, text, self))


def draw():
    layout = Layout()
    main_panel.Remi_PT_MainPanel.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout.entries


def entries_of(entries, kind):
    return [entry for entry in entries if entry[0] == kind]


remi.register()
try:
    wm = bpy.context.window_manager
    image_state = wm.remi_image
    image_settings = bpy.context.scene.remi_image
    image_settings.output = str(ROOT / "tests" / "__no_generated_images__")
    assert wm.remi_ui.workspace == "OPTIMIZE"
    choices = wm.remi_ui.bl_rna.properties["workspace"].enum_items
    assert {item.identifier for item in choices} == {"OPTIMIZE", "TEXTURE"}
    original = bpy.context.active_object
    bpy.context.view_layer.objects.active = None
    entries = draw()
    assert not entries_of(entries, "prop")
    operators = {entry[1]: entry for entry in entries_of(entries, "operator")}
    assert "remi.start_session" not in operators
    assert "remi.run_full_flow" not in operators
    assert "remi_board.open" in operators
    assert not {"remi.generate_image", "remi.add_image", "remi_board.apply_edit",
                "remi_board.return_to_board"} & operators.keys()
    assert not entries_of(entries, "popover")
    assert "Generate" not in [entry[2].get("text") for entry in entries_of(entries, "operator")]

    image_settings.prompt = "A test image"
    operators = {entry[1]: entry for entry in entries_of(draw(), "operator")}
    assert "remi.generate_image" not in operators
    header = Layout()
    editing.draw_edit_header(SimpleNamespace(layout=header), bpy.context)
    assert "remi_board.open" in {entry[1] for entry in entries_of(header.entries, "operator")}
    menu = Layout()
    board_workspace.draw_board_menu(SimpleNamespace(layout=menu), bpy.context)
    assert "remi_board.open" in {entry[1] for entry in entries_of(menu.entries, "operator")}
    with mock.patch.object(editing, "editing_scene", return_value=bpy.context.scene), \
         mock.patch.object(editing, "can_apply", return_value=True):
        header = Layout()
        editing.draw_edit_header(SimpleNamespace(layout=header), bpy.context)
        assert {"remi_board.apply_edit", "remi_board.return_to_board"} <= {
            entry[1] for entry in entries_of(header.entries, "operator")}
        assert not {"remi_board.apply_edit", "remi_board.return_to_board"} & {
            entry[1] for entry in entries_of(draw(), "operator")}
        assert "remi_board.open" not in {entry[1] for entry in entries_of(draw(), "operator")}
        menu = Layout()
        board_workspace.draw_board_menu(SimpleNamespace(layout=menu), bpy.context)
        assert "remi_board.return_to_board" in {entry[1] for entry in entries_of(menu.entries, "operator")}

    settings_layout = Layout()
    from remi.features.images.panel import Remi_PT_ImageSettings
    Remi_PT_ImageSettings.draw(SimpleNamespace(layout=settings_layout), bpy.context)
    properties = {entry[1] for entry in entries_of(settings_layout.entries, "prop")}
    assert {"python", "models", "output", "gaussians", "seed", "model", "size", "prompt"} & properties
    assert {"python", "models", "output", "gaussians", "seed"} <= properties
    assert ("preview_limit" in properties) != native_splats_available()
    with mock.patch.dict(generation_settings._LOCAL_DEFAULTS, {"python": "configured-python"}, clear=True):
        assert generation_settings.default_python() == "configured-python"
    with mock.patch.dict(generation_settings._LOCAL_DEFAULTS, {}, clear=True), \
         mock.patch.object(generation_settings.shutil, "which", return_value="detected-python"):
        assert generation_settings.default_python() == "detected-python"

    image_state.busy = True
    image_state.progress = 0.4
    image_state.status_kind = "PROGRESS"
    image_state.status = "Generating…"
    bpy.context.view_layer.objects.active = original
    wm.remi_ui.workspace = "OPTIMIZE"
    assert active_workspace(bpy.context) == "OPTIMIZE"
    assert not bpy.ops.remi.select_workspace.poll()
    assert not bpy.ops.remi.start_session.poll()
    assert not bpy.ops.remi.run_full_flow.poll()
    from remi.workflow.session import runtime as session_runtime
    try:
        session_runtime.begin(bpy.context, original)
    except RuntimeError as exc:
        assert "image generation" in str(exc)
    else:
        raise AssertionError("A mesh session must not hide an active image generation")
    assert not Remi_PT_EditToolsPanel.poll(bpy.context)
    entries = draw()
    operators = {entry[1] for entry in entries_of(entries, "operator")}
    assert not {"remi.cancel_image", "remi.generate_image"} & operators
    assert not any(entry[0] == "progress" for entry in entries)
    image_state.busy = False
    image_state.progress = 0.0
    image_state.status = ""
    image_state.status_kind = "IDLE"

    bpy.context.view_layer.objects.active = original
    assert bpy.ops.remi.select_workspace(workspace="OPTIMIZE") == {"FINISHED"}
    assert Remi_PT_EditToolsPanel.poll(bpy.context)
    operators = {entry[1] for entry in entries_of(draw(), "operator")}
    assert {"remi.start_session", "remi.run_full_flow"} <= operators
    assert "remi.generate_image" not in operators and "remi.add_image" not in operators

    wm.remi_ui.workspace = "TEXTURE"
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
