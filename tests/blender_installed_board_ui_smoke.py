"""Installed add-on startup, native launcher, editing, restart and lifecycle.

Driven by scripts/release.py smoke-ui. Never imports/registers the checkout or
uses the .bat launcher; only --prepare enables the installed extension once.
"""

import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy


MODULE = "bl_ext.user_default.remi"
OUTPUT = Path(os.environ["REMI_INSTALLED_UI_OUTPUT"]).resolve()
mode = sys.argv[sys.argv.index("--")+1]

if mode == "--prepare":
    config = Path(bpy.utils.user_resource("CONFIG")).resolve()
    assert config.is_relative_to(OUTPUT), "Never write normal Blender preferences from this test"
    assert bpy.ops.preferences.addon_enable(module=MODULE) == {"FINISHED"}
    addon = importlib.import_module(MODULE)
    canvas = importlib.import_module(MODULE+".features.board.canvas")
    assert canvas.BOARD is None
    bpy.context.preferences.view.show_splash = False
    # Test profile only: avoid the machine's slow WinTab driver at GUI startup.
    if sys.platform == "win32":
        bpy.context.preferences.inputs.tablet_api = "WINDOWS_INK"
    assert bpy.ops.wm.save_userpref() == {"FINISHED"}
    print("REMI_INSTALLED_BOARD_PREFERENCES_OK", flush=True)
else:
    assert mode in {"--first-run", "--restart"}
    assert MODULE in bpy.context.preferences.addons, "Enablement did not survive a normal Blender startup"
    assert MODULE in sys.modules, "Installed Remi did not load automatically"
    assert "remi" not in sys.modules, "The development checkout was loaded"
    addon = importlib.import_module(MODULE)
    canvas = importlib.import_module(MODULE+".features.board.canvas")
    workspace = importlib.import_module(MODULE+".features.board.workspace")
    editing = importlib.import_module(MODULE+".features.board.editing")
    toolbar = importlib.import_module(MODULE+".features.board.toolbar")
    Asset = importlib.import_module(MODULE+".features.board.assets").Asset
    repository = next(repo for repo in bpy.context.preferences.extensions.repos if repo.module == "user_default")
    assert Path(addon.__file__).resolve().is_relative_to(Path(repository.directory).resolve()/"remi")
    assert not (Path(addon.__file__).parent/"scripts").exists()
    assert "ui-sketch" not in bpy.utils.user_resource("CONFIG")
    assert bpy.ops.remi_board.open.poll()
    print(f"REMI_INSTALLED_BOARD_NORMAL_STARTUP_OK: {addon.__file__}", flush=True)

    window = bpy.context.window
    owner = window.scene
    original_workspace = window.workspace
    original_screen = window.screen
    original_areas = [(area.as_pointer(), area.type) for area in original_screen.areas]
    original_objects = set(owner.objects.keys())
    workspace_count = len(bpy.data.workspaces)
    step = waits = 0

    def mouse(kind, value, x, y, **kwargs):
        window.event_simulate(type=kind, value=value, x=x, y=y, **kwargs)

    def assert_single_board():
        assert sum(op is not None and op.bl_rna.identifier == "REMI_BOARD_OT_run"
                   for op in window.modal_operators) == 1
        assert len([item for item in bpy.data.workspaces if item.get(workspace.WORKSPACE_KEY)]) == 1

    def record(name):
        (OUTPUT/name).write_text(json.dumps({
            "installed_module": addon.__file__, "blender": bpy.app.version_string,
            "enabled_on_startup": True, "launcher": "F3: Open Remi Board",
            "development_launcher": False, "source_scene_unchanged": set(owner.objects.keys()) == original_objects,
            "assets": canvas.BOARD.catalog.documents(),
        }, indent=2), encoding="utf-8")

    def first_run():
        board = canvas.BOARD
        if step == 0:
            assert board is None and not any(item.get(workspace.WORKSPACE_KEY) for item in bpy.data.workspaces)
            area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
            region = canvas.window_region(area)
            x, y = region.x+region.width//2, region.y+region.height//2
            mouse("MOUSEMOVE", "NOTHING", x, y)
            mouse("F3", "PRESS", x, y)
            mouse("F3", "RELEASE", x, y)
        elif step == 1:
            for char in "Open Remi Board":
                kind = "SPACE" if char == " " else char.upper()
                window.event_simulate(type=kind, value="PRESS", unicode=char)
                window.event_simulate(type=kind, value="RELEASE")
        elif step == 2:
            bpy.ops.screen.screenshot(filepath=str(OUTPUT/"native-launcher.png"))
            window.event_simulate(type="RET", value="PRESS")
            window.event_simulate(type="RET", value="RELEASE")
        elif step == 3:
            if board is None or canvas.board_area(board) is None:
                return False
            assert len(bpy.data.workspaces) == workspace_count+1
            assert [(area.as_pointer(), area.type) for area in original_screen.areas] == original_areas
            assert len(bpy.context.window_manager.windows) == 1
            assert_single_board()
            assert len(toolbar.TOOLS) == 9 and not board.queue and board.runner.job is None
            image = bpy.data.images.new("Installed image", 32, 32)
            image.generated_color = (0.15, 0.4, 0.65, 1)
            image.filepath_raw = str(OUTPUT/"reference.png")
            image.file_format = "PNG"
            image.save()
            bpy.data.images.remove(image)
            area = canvas.board_area(board)
            with bpy.context.temp_override(window=window, area=area, region=canvas.window_region(area)):
                assert bpy.ops.remi_board.drop_images(filepath=str(OUTPUT/"reference.png")) == {"FINISHED"}
            mesh = OUTPUT/"mesh.ply"
            mesh.write_text("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
                            "element face 4\nproperty list uchar int vertex_indices\nend_header\n"
                            "-1 -1 0\n1 -1 0\n0 1 0\n0 0 1\n3 0 2 1\n3 0 1 3\n3 1 2 3\n3 2 0 3\n")
            asset = Asset("installed-mesh", "MESH", artifact=str(mesh), title="Installed mesh")
            preview = canvas.NativeMeshPreview(mesh, elevation=asset.elevation)
            try:
                board.capture_thumbnail(asset, preview)
            finally:
                preview.free()
            board.catalog.assets[asset.id] = asset
            board.positions[board.order[0]] = [0, 80]
            board.positions[asset.id] = [350, 80]
            board.scales[board.order[0]] = board.scales[asset.id] = 0.8
            board.order.append(asset.id)
            board.selected = {asset.id}
            board.zoom, board.pan = 0.8, [100, 300]
            canvas.save_layout(bpy.context)
        elif step == 4:
            region = canvas.window_region(canvas.board_area(board))
            x, y, w, h = board.card_rect("installed-mesh")
            x, y = int(region.x+x+w/2), int(region.y+y+h/2)
            mouse("MOUSEMOVE", "NOTHING", x, y)
            mouse("LEFTMOUSE", "PRESS", x, y, alt=True)
            mouse("MOUSEMOVE", "NOTHING", x+60, y+25, alt=True)
        elif step == 5:
            if "installed-mesh" not in board.loaded_previews:
                assert not board.preview_errors, board.preview_errors
                return False
            assert board.catalog.assets["installed-mesh"].yaw > 300
            window.event_simulate(type="LEFTMOUSE", value="RELEASE", alt=True)
        elif step == 6:
            if board.live_asset_id or board.orbit_drag is not None:
                return False
            assert board.thumbnail_ready(board.catalog.assets["installed-mesh"])
            assert set(owner.objects.keys()) == original_objects
            bpy.ops.screen.screenshot(filepath=str(OUTPUT/"installed-board.png"))
            assert bpy.ops.remi_board.edit_mesh(asset_id="installed-mesh") == {"FINISHED"}
        elif step == 7:
            if window.scene == owner:
                return False
            assert editing.editing_scene(bpy.context) is not None
            mesh = bpy.context.view_layer.objects.active
            assert mesh.type == "MESH"
            mesh.data.vertices[0].co.z += 0.2
            assert bpy.ops.remi_board.apply_edit() == {"FINISHED"}
        elif step == 8:
            if window.scene != owner or canvas.board_area(board) is None:
                return False
            asset = board.catalog.assets["installed-mesh"]
            assert Path(asset.artifact).suffix == ".glb" and len(asset.revisions) == 1
            assert Path(asset.revisions[0]["artifact"]).is_file()
            assert set(owner.objects.keys()) == original_objects
            assert_single_board()
            assert bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT/"installed-board.blend")) == {"FINISHED"}
            assert not board.loaded_previews
            record("first-run.json")
            print("REMI_INSTALLED_BOARD_FIRST_RUN_OK", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        return True

    def restart():
        board = canvas.BOARD
        if step == 0:
            if board is None or canvas.board_area(board) is None:
                return False
            assert_single_board()
            assert board.catalog.assets["installed-mesh"].artifact.endswith(".glb")
            assert len(board.catalog.assets["installed-mesh"].revisions) == 1
            assert not board.queue and board.runner.job is None
            assert not any("isolated native preview" in scene.name for scene in bpy.data.scenes)
            assert bpy.ops.preferences.addon_disable(module=MODULE) == {"FINISHED"}
            assert canvas.BOARD is None
            assert not bpy.app.timers.is_registered(workspace.monitor_workspace)
            assert not bpy.app.timers.is_registered(canvas.board_tick)
            assert not hasattr(bpy.types.Scene, "remi_generation")
        elif step == 1:
            assert bpy.ops.preferences.addon_enable(module=MODULE) == {"FINISHED"}
        elif step == 2:
            if board is None or canvas.board_area(board) is None:
                return False
            assert len(bpy.data.workspaces) == workspace_count
            assert_single_board()
            assert set(owner.objects.keys()) == original_objects
            bpy.ops.screen.screenshot(filepath=str(OUTPUT/"restart.png"))
            record("restart.json")
            print("REMI_INSTALLED_BOARD_UI_SMOKE_OK", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        return True

    def guarded():
        global step, waits
        try:
            waits += 1
            assert waits < 150, f"Timed out at {mode} step {step}"
            result = first_run() if mode == "--first-run" else restart()
            if result is None:
                return None
            if result:
                step += 1
                waits = 0
            return 0.4
        except Exception:
            traceback.print_exc()
            (OUTPUT/"failed.txt").write_text(traceback.format_exc(), encoding="utf-8")
            bpy.ops.screen.screenshot(filepath=str(OUTPUT/"failed.png"))
            print(f"REMI_INSTALLED_BOARD_UI_SMOKE_FAILED: {mode} step {step}", flush=True)
            os._exit(1)

    bpy.app.timers.register(guarded, first_interval=0.6)
