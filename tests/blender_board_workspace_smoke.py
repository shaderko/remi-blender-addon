"""Real-window board regression. Run with --enable-event-simulate (not -b).

Uses temporary images and a fake image worker; no model downloads or inference.
"""

from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace
from unittest import mock

import bpy
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_board_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()

from remi.features.board import canvas, workspace
from remi.features.images import operators as image_operators
from remi.features.generation import operators as generation_operators
from remi.features.board.assets import OBJECT_ASSET_KEY
from remi.features.board import jobs as board_jobs


window = bpy.context.window
original_workspace = window.workspace
original_screen = window.screen
original_areas = [(area.as_pointer(), area.type) for area in original_screen.areas]
original_objects = set(bpy.data.objects.keys())
original_windows = len(bpy.context.window_manager.windows)
image_output = OUTPUT / "images"
bpy.context.scene.remi_image.output = str(image_output)


def image_job(index, prompt, *, complete=True):
    directory = image_output / f"job_{index}"
    directory.mkdir(parents=True)
    image = bpy.data.images.new(f"fixture {index}", 128, 128)
    image.generated_color = [(0.19, 0.38, 0.56, 1), (0.58, 0.29, 0.14, 1), (0.3, 0.52, 0.35, 1)][index % 3]
    image.filepath_raw = str(directory / "image_000.png")
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    (directory / "meta.json").write_text(json.dumps({
        "prompt": prompt, "model": "base", "images": [{"file": "image_000.png"}],
        "width": 128, "height": 128,
    }), encoding="utf-8")
    if complete:
        (directory / "complete.json").write_text("{}", encoding="utf-8")
    return directory


for index in range(3):
    image_job(index, f"Fixture {index}")


class FakeJob:
    def __init__(self, directory):
        self.directory = directory
        self.done = False

    def status(self):
        return {"phase": "Generating image 1/1", "percent": 50}

    def poll(self):
        return 0 if self.done else None

    def result(self):
        (self.directory / "complete.json").write_text("{}", encoding="utf-8")
        return self.directory

    def cancel(self):
        pass


jobs = []


def start_fake(**kwargs):
    job = FakeJob(image_job(len(jobs) + 3, kwargs["prompt"], complete=False))
    jobs.append(job)
    return job


patcher = mock.patch.object(image_operators.IrisJob, "start", side_effect=start_fake)
patcher.start()
splat_directory = OUTPUT / "splats"
splat_directory.mkdir()
splat_path = splat_directory / "splats.npz"
np.savez_compressed(splat_path,
    schema=np.array(1, dtype=np.int32),
    positions=np.array([[0, 0, 0], [0.5, 0, 0], [0, 0.5, 0]], dtype=np.float32),
    scales=np.full((3, 3), 0.1, dtype=np.float32),
    rotations=np.tile(np.array([[1, 0, 0, 0]], dtype=np.float32), (3, 1)),
    opacities=np.ones(3, dtype=np.float32),
    sh_dc=np.array([[1, 0, -2], [0, 1, 0], [0, 0, 1]], dtype=np.float32))


class FakeSplatJob(FakeJob):
    def result(self):
        return splat_path


splat_job = FakeSplatJob(splat_directory)
splat_patcher = mock.patch.object(generation_operators.GenerationJob, "start", return_value=splat_job)
splat_patcher.start()
mesh_directory = OUTPUT / "mesh"
mesh_directory.mkdir()
mesh_path = mesh_directory / "mesh.ply"
mesh_path.write_text("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
    "property uchar red\nproperty uchar green\nproperty uchar blue\nelement face 4\nproperty list uchar int vertex_indices\nend_header\n"
    "-1 -1 0 90 150 200\n1 -1 0 90 150 200\n0 1 0 90 150 200\n0 0 1 90 150 200\n"
    "3 0 2 1\n3 0 1 3\n3 1 2 3\n3 2 0 3\n", encoding="ascii")


class FakeMeshJob(FakeJob):
    def result(self):
        return mesh_path


mesh_job = FakeMeshJob(mesh_directory)
mesh_job.done = True
mesh_patcher = mock.patch.object(board_jobs.MeshJob, "start", return_value=mesh_job)
mesh_patcher.start()
step = 0
waits = 0
board_workspace = None
board_space = None
splat_image = None
splat_asset = None
generated_name = None
mesh_asset = None
flow_asset = None
scene_before_flow = None


def simulate(key, *, ctrl=False, shift=False, area=None):
    area = area or canvas.board_area(canvas.BOARD)
    region = canvas.window_region(area)
    x, y = region.x + region.width // 2, region.y + region.height // 2
    window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
    window.event_simulate(type=key, value="PRESS", unicode="1" if key == "ONE" else "", ctrl=ctrl, shift=shift, x=x, y=y)
    window.event_simulate(type=key, value="RELEASE", ctrl=ctrl, shift=shift, x=x, y=y)


def advance():
    global step, waits, board_workspace, board_space, splat_image, splat_asset, generated_name, mesh_asset, flow_asset, scene_before_flow
    waits += 1
    assert waits < 150, f"Timed out at step {step}"
    if step == 0:
        bpy.ops.remi_board.open()
    elif step == 1:
        if canvas.BOARD is None:
            return 0.2
        board_workspace = window.workspace
        board_space = canvas.BOARD.space
        assert board_workspace.get(workspace.WORKSPACE_KEY)
        assert len(bpy.context.window_manager.windows) == original_windows
        assert [(area.as_pointer(), area.type) for area in original_screen.areas] == original_areas
        board_area = canvas.board_area(canvas.BOARD)
        viewport = next(area for area in window.screen.areas if area.type == "VIEW_3D")
        assert viewport != board_area and board_area.x < viewport.x
        assert board_area.spaces.active.show_region_header
        assert len(canvas.BOARD.entries) == 3
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "docked.png"))
        # A rendered photo must preserve its file's colors, not apply a second
        # display gamma transform or the built-in UI shader's inverse transform.
        sample_id = canvas.BOARD.order[0]
        sample = bpy.data.images.load(canvas.BOARD.catalog.assets[sample_id].artifact, check_existing=False)
        captured = bpy.data.images.load(str(OUTPUT / "docked.png"), check_existing=False)
        try:
            region = canvas.window_region(board_area)
            x, y, w, h = canvas.BOARD.card_rect(sample_id)
            index = (int(region.y+y+h/2)*captured.size[0] + int(region.x+x+w/2))*4
            source_index = ((sample.size[1]//2)*sample.size[0] + sample.size[0]//2)*4
            actual = list(captured.pixels[index:index+3])
            expected = list(sample.pixels[source_index:source_index+3])
            np.testing.assert_allclose(actual, expected, atol=0.015)
        finally:
            bpy.data.images.remove(captured)
            bpy.data.images.remove(sample)

        # Test routing independently of the current mouse/region context.
        operator = SimpleNamespace(_board=canvas.BOARD,
                                   _release_input=canvas.REMI_BOARD_OT_run._release_input)
        region = canvas.window_region(viewport)
        event = SimpleNamespace(type="A", value="PRESS", mouse_x=region.x + 50,
                                mouse_y=region.y + 50, ctrl=False, shift=False)
        assert canvas.REMI_BOARD_OT_run.modal(operator, bpy.context, event) == {"PASS_THROUGH"}
        region = canvas.window_region(board_area)
        event.mouse_x, event.mouse_y = region.x + 50, region.y + 50
        for event.type, event.ctrl in (("SPACE", True), ("PAGE_UP", True), ("TIMER", False), ("F3", False)):
            assert canvas.REMI_BOARD_OT_run.modal(operator, bpy.context, event) == {"PASS_THROUGH"}
        # Double-click inspection must not create another window.
        path = canvas.BOARD.order[0]
        assert canvas.focus_image(canvas.BOARD, path)
        canvas.BOARD.image_focus = None
        canvas.BOARD.fit()
        canvas.BOARD.focused = True
        simulate("SPACE", ctrl=True)
    elif step == 2:
        assert len(window.screen.areas) == 1, "Maximize Area shortcut was swallowed"
        assert canvas.board_area(canvas.BOARD) is not None, "Canvas lost its host after maximize"
        assert canvas.BOARD.space == board_space
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "maximized.png"))
        simulate("SPACE", ctrl=True)
    elif step == 3:
        assert len(window.screen.areas) == len(original_areas) + 1
        assert canvas.board_area(canvas.BOARD) is not None
        canvas.BOARD.focused = True
        simulate("PAGE_UP", ctrl=True)
    elif step == 4:
        assert window.workspace != board_workspace, "Workspace shortcut was swallowed"
        assert canvas.BOARD is not None
        assert canvas.board_area(canvas.BOARD) is None
        assert len(bpy.context.window_manager.windows) == original_windows
        window.workspace = board_workspace
    elif step == 5:
        assert canvas.board_area(canvas.BOARD) is not None
        viewport = next(area for area in window.screen.areas if area.type == "VIEW_3D")
        for key in ("G", "X", "ONE", "RET"):
            simulate(key, area=viewport)
    elif step == 6:
        assert abs(bpy.data.objects["Cube"].location.x - 1.0) < 1e-5, "Viewport transform was swallowed"
        # Queue two jobs through the real production modal operator.
        # Temporarily busy Remi operations must pause, not discard, the queue.
        bpy.context.window_manager.remi_session.active = True
        try:
            canvas.BOARD.composer = "First queued image"
            assert canvas.submit_current(bpy.context)
            canvas.BOARD.composer = "Second queued image"
            assert canvas.submit_current(bpy.context)
            assert len(canvas.BOARD.queue) == 2 and not jobs
        finally:
            bpy.context.window_manager.remi_session.active = False
    elif step == 7:
        if not jobs:
            return 0.2
        assert bpy.context.window_manager.remi_image.busy
        window.workspace = original_workspace
        jobs[0].done = True
    elif step == 8:
        if len(jobs) < 2:
            return 0.2
        assert window.workspace == original_workspace
        assert canvas.BOARD.running == "Second queued image"
        jobs[1].done = True
    elif step == 9:
        if bpy.context.window_manager.remi_image.busy or canvas.BOARD.running:
            return 0.2
        assert not canvas.BOARD.queue and not canvas.BOARD.pending
        assert len(canvas.BOARD.entries) == 5
        assert set(bpy.data.objects.keys()) == original_objects
        splat_image = canvas.BOARD.order[0]
        canvas.BOARD.selected = {splat_image}
        assert canvas.start_splat(bpy.context)
        splat_asset = next(iter(canvas.BOARD.selected))
        assert splat_asset != splat_image
        assert canvas.BOARD.catalog.assets[splat_asset].parent == splat_image
        assert canvas.BOARD.catalog.assets[splat_image].kind == "IMAGE"
        assert splat_image in canvas.BOARD.order
        splat_job.done = True
    elif step == 10:
        if canvas.BOARD.catalog.assets[splat_asset].state in {"QUEUED", "RUNNING"}:
            return 0.2
        assert window.workspace == original_workspace
        assert canvas.BOARD.catalog.assets[splat_asset].state == "READY", canvas.BOARD.catalog.assets[splat_asset].status
        assert set(bpy.data.objects.keys()) == original_objects, "Generating on the board modified the scene"
        window.workspace = board_workspace
    elif step == 11:
        if not canvas.BOARD.thumbnail_ready(canvas.BOARD.catalog.assets[splat_asset]):
            assert splat_asset not in canvas.BOARD.preview_errors, canvas.BOARD.preview_errors
            return 0.2
        assert not canvas.BOARD.loaded_previews, "Idle cards eagerly loaded native previews"
        assert canvas.add_asset_to_scene(bpy.context, splat_asset)
        generated = bpy.context.view_layer.objects.active
        generated_name = generated.name
        assert generated[OBJECT_ASSET_KEY] == splat_asset
        assert not generated.hide_viewport and not generated.hide_get()
        assert bpy.context.view_layer.objects.active == generated
    elif step == 12:
        assert canvas.board_area(canvas.BOARD) is not None
        assert len(bpy.context.window_manager.windows) == original_windows
        generated = bpy.data.objects[generated_name]
        generated.name = "Renamed board asset"
        generated_name = generated.name
        count = len(bpy.data.objects)
        assert canvas.add_asset_to_scene(bpy.context, splat_asset)
        assert len(bpy.data.objects) == count, "Renaming the imported asset caused a duplicate"
        # Reopening is idempotent: no new tabs, splits, windows, or modal handler.
        workspaces = len(bpy.data.workspaces)
        bpy.ops.remi_board.open()
        assert len(bpy.data.workspaces) == workspaces
    elif step == 13:
        assert bpy.ops.remi_board.create_mesh("INVOKE_DEFAULT", asset_id=splat_asset) == {"RUNNING_MODAL"}
        simulate("RET")
    elif step == 14:
        if mesh_asset is None:
            candidate = next(iter(canvas.BOARD.selected))
            if candidate == splat_asset:
                return 0.2
            mesh_asset = candidate
            assert canvas.BOARD.catalog.assets[mesh_asset].parent == splat_asset
        asset = canvas.BOARD.catalog.assets[mesh_asset]
        if asset.state in {"QUEUED", "RUNNING"}:
            return 0.2
        assert asset.state == "READY", asset.status
        assert len(bpy.data.objects) == len(original_objects) + 1, "Meshing on the board imported an object"
        # Run a real UV-only automatic flow in an isolated Blender process.
        from remi.app.flow import initialize_steps
        initialize_steps(bpy.context.scene.remi_flow, ("UV",))
        bpy.context.scene.remi_flow.preset = "CURRENT"
        bpy.context.scene.remi_settings.bake_texture_size = 256
        scene_before_flow = [(obj.name, obj.matrix_world.copy()) for obj in bpy.context.scene.objects]
        assert bpy.ops.remi_board.run_flow("INVOKE_DEFAULT", asset_id=mesh_asset) == {"RUNNING_MODAL"}
        simulate("RET")
    elif step == 15:
        if flow_asset is None:
            candidate = next(iter(canvas.BOARD.selected))
            if candidate == mesh_asset:
                return 0.2
            flow_asset = candidate
            assert canvas.BOARD.catalog.assets[flow_asset].parent == mesh_asset
            window.workspace = original_workspace
            return 0.2
        asset = canvas.BOARD.catalog.assets[flow_asset]
        if asset.state in {"QUEUED", "RUNNING"}:
            return 0.2
        assert asset.state == "READY", asset.status
        assert Path(asset.artifact).is_file() and Path(asset.preview).is_file()
        assert not bpy.context.window_manager.remi_session.active
        assert len(bpy.context.scene.objects) == len(scene_before_flow)
        assert all(bpy.data.objects[name].matrix_world == matrix for name, matrix in scene_before_flow)
        assert canvas.BOARD.catalog.assets[splat_image].kind == "IMAGE"
        assert canvas.BOARD.catalog.assets[splat_asset].kind == "SPLAT"
        window.workspace = board_workspace
    elif step == 16:
        # Removing a source card is non-destructive to its files and descendants.
        source_file = Path(canvas.BOARD.catalog.assets[splat_image].artifact)
        assert bpy.ops.remi_board.delete(path=splat_image, delete_files=False) == {"FINISHED"}
        assert source_file.is_file()
        assert splat_image not in canvas.BOARD.order
        assert mesh_asset in canvas.BOARD.order and flow_asset in canvas.BOARD.order
        # Restore it for the visual screenshot; hide state is persisted normally.
        canvas.BOARD.catalog.assets[splat_image].hidden = False
        canvas.BOARD.order.insert(0, splat_image)
        canvas.BOARD.positions[splat_image] = [0, 0]
        canvas.BOARD.fit()
    elif step == 17:
        assert len(window.screen.areas) == len(original_areas) + 1
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "finished.png"))
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / "workspace.blend"))
        bpy.ops.wm.open_mainfile(filepath=str(OUTPUT / "workspace.blend"))
    elif step == 18:
        if canvas.BOARD is None:
            return 0.2
        assert canvas.board_area(canvas.BOARD) is not None
        assert len(canvas.BOARD.entries) == 5
        assert canvas.BOARD.catalog.assets[splat_asset].parent == splat_image
        assert canvas.BOARD.catalog.assets[splat_image].kind == "IMAGE"
        assert canvas.BOARD.catalog.assets[mesh_asset].parent == splat_asset
        assert canvas.BOARD.catalog.assets[flow_asset].parent == mesh_asset
        assert canvas.BOARD.catalog.assets[flow_asset].state == "READY"
        assert not canvas.BOARD.loaded_previews
        with mock.patch.object(canvas, "Preview", side_effect=AssertionError("Reload rendered a cached model")):
            canvas.BOARD.prepare_preview()
        assert generated_name in bpy.data.objects
        patcher.stop()
        splat_patcher.stop()
        mesh_patcher.stop()
        remi.unregister()
        assert canvas.BOARD is None
        assert not bpy.app.timers.is_registered(canvas.board_tick)
        assert not bpy.app.timers.is_registered(workspace.monitor_workspace)
        assert len(bpy.context.window_manager.windows) == original_windows
        assert generated_name in bpy.data.objects, "Closing the canvas removed a generated asset"
        (OUTPUT / "passed.txt").write_text("Docking, routing, viewport transforms, native maximize/workspace shortcuts, hidden-workspace image/3D generation, Add 3D, save/reload, and unregister passed.\n")
        print(f"REMI_BOARD_WORKSPACE_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return 0.8


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        print([(area.type, area.x, area.y, area.width, area.height) for area in window.screen.areas], flush=True)
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        print(f"REMI_BOARD_WORKSPACE_SMOKE_FAILED: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=0.8, persistent=True)
