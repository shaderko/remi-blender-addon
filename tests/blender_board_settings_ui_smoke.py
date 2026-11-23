"""Real settings rendering, shared RNA edits and tool tab entry points."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.mkdtemp(prefix="remi-settings-ui-"))
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
bpy.context.scene.remi_image.output = str(OUTPUT / "images")
bpy.context.scene.remi_generation.output = str(OUTPUT / "models")
bpy.context.preferences.filepaths.temporary_directory = str(OUTPUT)
from remi.features.board import canvas, settings_window
from remi.features.board.settings_sections import settings_items, choices, SECTIONS
from remi.features import cloud
if bpy.app.timers.is_registered(cloud.tick): bpy.app.timers.unregister(cloud.tick)
bpy.context.window_manager.remi_cloud.signed_in = True
bpy.context.window_manager.remi_cloud.email = "artist@example.com"

step = waits = 0


def current(name):
    return next(item for item in settings_items(bpy.context, settings_window.active_window.section)
                if item.name == name)


def screenshot(name):
    bpy.ops.screen.screenshot(filepath=str(OUTPUT / name))


def advance():
    global step, waits
    try:
        if step == 0:
            assert bpy.ops.remi_board.open() == {"FINISHED"}
        elif step == 1:
            area = canvas.board_area(canvas.BOARD) if canvas.BOARD is not None else None
            if area is None:
                waits += 1
                assert waits < 25
                return .2
            region = canvas.window_region(area)
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.remi_board.settings("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
            host = settings_window.active_window
            host.activate(bpy.context, ("property", current("use_cloud")))
            assert bpy.context.scene.remi_generation.backend == "API"
        elif step == 2:
            screenshot("settings-general.png")
            host = settings_window.active_window
            account_actions = {item.operator:item for item in settings_items(bpy.context,"general") if item.kind == "action"}
            assert {"remi.cloud_account","remi.cloud_logout"} <= account_actions.keys()
            with patch.object(cloud.webbrowser,"open") as opened:
                host.activate(bpy.context,("operator",account_actions["remi.cloud_account"]))
                opened.assert_called_once_with("https://remi3d.app/app/profile",new=2)
                assert settings_window.active_window is host
            with patch.object(cloud.runtime,"command") as command:
                host.activate(bpy.context,("operator",account_actions["remi.cloud_logout"]))
                command.assert_called_once_with("logout",bpy.context.window_manager.remi_cloud.base_url)
                assert settings_window.active_window is host
            host.activate(bpy.context, ("section", "image"))
            host.activate(bpy.context, ("property", current("count")))
            host.edit_text = "3"
            assert host.commit(bpy.context)
            assert bpy.context.scene.remi_image.count == 3
            host.activate(bpy.context, ("property", current("size")))
            host.activate(bpy.context, ("choice", (current("size"), choices(current("size"), bpy.context)[0][0])))
            assert host.dropdown is None
            bpy.context.window_manager.remi_image.busy = True
            host.activate(bpy.context, ("step", (current("count"), 1)))
            assert bpy.context.scene.remi_image.count == 3
            bpy.context.window_manager.remi_image.busy = False
        elif step == 3:
            screenshot("settings-image.png")
            settings_window.active_window.finish()
            area = canvas.board_area(canvas.BOARD)
            with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
                assert bpy.ops.remi_board.tool_settings("INVOKE_DEFAULT", tool="generate3d") == {"FINISHED"}
            assert settings_window.active_window.section == "generate3d"
        elif step == 4:
            screenshot("settings-3d.png")
            host = settings_window.active_window
            assert len([action for _,action in host.actions if action[0] == "section"]) == len(SECTIONS)
            host.activate(bpy.context,("section","mesh"))
            assert current("mesh_quality")
            host.activate(bpy.context,("section","generate3d"))
            seed = bpy.context.scene.remi_generation.seed
            host.activate(bpy.context, ("step", (current("seed"), 1)))
            assert bpy.context.scene.remi_generation.seed == seed+1
            host.finish()
            area = canvas.board_area(canvas.BOARD)
            with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
                bpy.ops.remi_board.settings("INVOKE_DEFAULT", section="flow")
            flow = bpy.context.scene.remi_flow
            flow.preset = "CURRENT"
            items = settings_items(bpy.context,"flow")
            first = next(item for item in items if item.name == "enabled")
            first.assign(False)
            assert not next(item for item in settings_items(bpy.context,"flow") if item.name == "enabled").value
        elif step == 5:
            screenshot("settings-flow.png")
            host = settings_window.active_window
            host.scroll = host.max_scroll
        elif step == 6:
            screenshot("settings-flow-scroll.png")
            host = settings_window.active_window
            host.section, host.scroll = "general", 0
            current("use_cloud").assign(False)
            host.section = "generate3d"
            for section in SECTIONS:
                for item in settings_items(bpy.context, section):
                    if item.kind == "property":
                        assert item.rna.type in {"BOOLEAN", "ENUM", "STRING", "INT", "FLOAT"}
                        if item.rna.type == "ENUM": assert choices(item, bpy.context)
            host.dropdown = next(item for item in settings_items(bpy.context,"generate3d") if item.name == "engine")
        elif step == 7:
            screenshot("settings-local-dropdown.png")
            settings_window.active_window.finish()
            with patch.object(cloud.webbrowser, "open") as opened:
                assert bpy.ops.remi.cloud_account() == {"FINISHED"}
                opened.assert_called_once_with("https://remi3d.app/app/profile", new=2)
            area = canvas.board_area(canvas.BOARD)
            with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
                bpy.ops.remi_board.settings("INVOKE_DEFAULT", section="general")
                settings_window.active_window.activate(bpy.context, ("path", current("python")))
            assert settings_window.active_window.picking
        elif step == 8:
            screenshot("settings-file-picker.png")
            browser_window, browser_area = next((window, area) for window in bpy.context.window_manager.windows
                                                for area in window.screen.areas if area.type == "FILE_BROWSER")
            region = next(r for r in browser_area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=browser_window, area=browser_area, region=region):
                bpy.ops.file.cancel()
        elif step == 9:
            assert not settings_window.active_window.picking
            settings_window.active_window.finish()
            assert not any(asset.state in {"QUEUED", "RUNNING"} for asset in canvas.BOARD.catalog.assets.values())
            (OUTPUT / "passed.txt").write_text("General account controls, persistent navigation, shared values, tool tabs, dropdowns, file-picker lifecycle.")
            print(f"REMI_SETTINGS_UI_SMOKE_OK: {OUTPUT}", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 1.0
    except Exception:
        (OUTPUT / "error.txt").write_text(traceback.format_exc())
        traceback.print_exc()
        os._exit(1)


bpy.app.timers.register(advance, first_interval=1.0)
