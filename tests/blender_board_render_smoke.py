"""Pixel regressions for textured shader/BLF leakage into flat board UI.

Run with --factory-startup --enable-event-simulate. REMI_RENDER_INSTALLED=1
uses the installed extension. Synthetic atlas only; no inference or baking.
"""

from datetime import datetime
import importlib
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
import gpu
from mathutils import Matrix
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_render_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
installed = os.environ.get("REMI_RENDER_INSTALLED") == "1"
package = "bl_ext.user_default.remi" if installed else "remi"
if installed:
    assert bpy.ops.preferences.addon_enable(module=package) == {"FINISHED"}
    remi = importlib.import_module(package)
else:
    spec = importlib.util.spec_from_file_location(package, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules[package] = remi
    spec.loader.exec_module(remi)
    remi.register()
canvas = importlib.import_module(package + ".features.board.canvas")
organizing = importlib.import_module(package + ".features.board.organizing")
organization = importlib.import_module(package + ".features.board.organization")
Asset = importlib.import_module(package + ".features.board.assets").Asset

window, scene = bpy.context.window, bpy.context.scene
scene.remi_image.output = str(OUTPUT / "images")
scene.remi_generation.output = str(OUTPUT / "models")
yy, xx = np.mgrid[:128, :128]
atlas_pixels = np.ones((128, 128, 4), np.float32)
atlas_pixels[:, :, :3] = np.where(((xx//16+yy//16) % 2)[:, :, None], [.95, .08, .65], [.08, .9, .15])
image = bpy.data.images.new("Unrelated native image-editor atlas", 128, 128)
image.pixels.foreach_set(atlas_pixels.ravel())
image.filepath_raw, image.file_format = str(OUTPUT / "atlas.png"), "PNG"
image.save()
step = waits = 0
underlay = None
texture = None
group = ""


def gpu_checks():
    global texture
    texture = gpu.types.GPUTexture((128, 128), format="RGBA32F",
                                   data=gpu.types.Buffer("FLOAT", atlas_pixels.size, atlas_pixels.ravel()))
    offscreen = gpu.types.GPUOffScreen(640, 480, format="RGBA32F")
    background = np.array([.06, .08, .1])
    report = {"backend": gpu.platform.backend_type_get(), "renderer": gpu.platform.renderer_get(), "cases": {}}
    blend, depth, mask = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
    viewport = gpu.state.viewport_get()
    try:
        with offscreen.bind(), gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
            gpu.state.viewport_set(0, 0, 640, 480)
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(False)
            gpu.matrix.load_identity()
            gpu.matrix.load_projection_matrix(Matrix(((2/640, 0, 0, -1), (0, 2/480, 0, -1),
                                                       (0, 0, -1, 0), (0, 0, 0, 1))))
            frame = gpu.state.active_framebuffer_get()
            def seed():
                frame.clear(color=(*background, 1))
                canvas.draw_image(texture, 4, 4, 24, 24)
                canvas.draw_text("Native label", 500, 440, 12)
                canvas.draw_image(texture, 4, 4, 24, 24)
                gpu.state.blend_set("NONE")  # Native/BLF draws may leave this state.
            def pixels():
                return np.array(frame.read_color(0, 0, 640, 480, 4, 0, "FLOAT"), dtype=np.float32).reshape(480, 640, 4)
            tint = np.array(canvas.ACCENT[:3])
            expected = background*.9+tint*.1
            seed()
            canvas.draw_rect(60, 60, 100, 100, (*tint, .1))
            sampled = pixels()[80:140, 80:140, :3]
            report["cases"]["flat_rect_after_texture"] = float(np.max(np.abs(sampled-expected)))
            seed()
            canvas.draw_rects(((60, 60, 100, 100), (200, 60, 100, 100)), (*tint, .1))
            sampled = pixels()[80:140, 220:280, :3]
            report["cases"]["flat_batch_after_texture"] = float(np.max(np.abs(sampled-expected)))
            seed()
            original_board = canvas.BOARD
            try:
                canvas.BOARD = SimpleNamespace(region=(640, 480))
                canvas.draw_curve((200, 100), (300, 100), (*tint, .1), width=3)
            finally:
                canvas.BOARD = original_board
            sampled = pixels()[99:101, 245:255, :3]
            report["cases"]["flat_curve_after_texture"] = float(np.max(np.abs(sampled-expected)))
            seed()
            model = organization.Organization([
                {"id": "group:a", "title": "First", "anchor": [50, 70]},
                {"id": "group:b", "title": "Second", "anchor": [330, 70]}])
            fake = SimpleNamespace(organization=model, catalog=SimpleNamespace(assets={}), positions={}, scales={},
                                   zoom=1, pan=[0, 0], region=(640, 480), group_selected=set(), group_drop=None)
            organizing.draw(fake)
            expected_group = background*.55+np.array([.12, .18, .22])*.45
            sampled = pixels()[90:140, 370:450, :3]
            report["cases"]["second_group_after_text"] = float(np.max(np.abs(sampled-expected_group)))
            (OUTPUT / "gpu-pixels.json").write_text(json.dumps(report, indent=2))
            assert all(error < .002 for error in report["cases"].values()), report
    finally:
        gpu.state.viewport_set(*viewport)
        gpu.state.blend_set(blend)
        gpu.state.depth_test_set(depth)
        gpu.state.depth_mask_set(mask)
        offscreen.free()


def host_underlay():
    board = canvas.BOARD
    if board is not None and bpy.context.area == canvas.board_area(board):
        region = bpy.context.region
        canvas.draw_image(texture, 0, 0, region.width, region.height)
        gpu.state.blend_set("NONE")


def mouse(kind, value, point):
    region = canvas.window_region(canvas.board_area(canvas.BOARD))
    window.event_simulate(type=kind, value=value, x=int(region.x+point[0]), y=int(region.y+point[1]))


def capture(name):
    filepath = OUTPUT / name
    bpy.ops.screen.screenshot(filepath=str(filepath))
    captured = bpy.data.images.load(str(filepath), check_existing=False)
    try:
        result = np.array(captured.pixels, np.float32).reshape(captured.size[1], captured.size[0], 4).copy()
    finally:
        bpy.data.images.remove(captured)
    return result


def check_state_guard(board):
    original = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
    try:
        area = canvas.board_area(board)
        for fail in (False, True):
            gpu.state.blend_set("ADDITIVE")
            gpu.state.depth_test_set("LESS")
            gpu.state.depth_mask_set(True)
            with bpy.context.temp_override(area=area, region=canvas.window_region(area)):
                with mock.patch.object(canvas, "_draw_board", side_effect=RuntimeError("injected draw failure") if fail else None):
                    try:
                        canvas.board_draw()
                    except RuntimeError:
                        assert fail
            assert gpu.state.blend_get() == "ADDITIVE"
            assert gpu.state.depth_test_get() == "LESS" and gpu.state.depth_mask_get()
    finally:
        gpu.state.blend_set(original[0])
        gpu.state.depth_test_set(original[1])
        gpu.state.depth_mask_set(original[2])


def advance():
    global step, waits, underlay, group
    waits += 1
    assert waits < 120, f"Timed out at step {step}"
    board = canvas.BOARD
    if step == 0:
        gpu_checks()
        underlay = bpy.types.SpaceImageEditor.draw_handler_add(host_underlay, (), "WINDOW", "POST_PIXEL")
        bpy.ops.remi_board.open()
    elif step == 1:
        if board is None:
            return .2
        for index, key in enumerate(("a", "b")):
            board.catalog.assets[key] = Asset(key, "IMAGE", artifact=str(OUTPUT / "atlas.png"), title="Card " + key)
            board.order.append(key)
            board.positions[key] = [120+index*240, 180]
            board.scales[key] = .45
        group = board.organization.create({"a", "b"}, title="Clean group fill").id
        board.zoom, board.pan = 1, [100, 200]
        canvas.board_area(board).spaces.active.image = image
    elif step == 2:
        if any(board.texture(key) is None for key in ("a", "b")):
            return .2
        check_state_guard(board)
        mouse("MOUSEMOVE", "NOTHING", (130, 250))
        mouse("LEFTMOUSE", "PRESS", (130, 250))
        mouse("MOUSEMOVE", "NOTHING", (750, 700))
    elif step == 3:
        assert board.box_drag is not None and board.selected == {"a", "b"}
        result = capture("marquee-no-atlas.png")
        region = canvas.window_region(canvas.board_area(board))
        # Blank selection space must be a uniform, faint tint, not a texture.
        sampled = result[region.y+610:region.y+660, region.x+620:region.x+690, :3]
        assert np.max(sampled[:, :, 0]) < .7, "Selection fill became opaque orange"
        assert np.quantile(sampled[:, :, 0], .9)-np.quantile(sampled[:, :, 0], .1) < .08, "Atlas leaked into marquee"
        mouse("LEFTMOUSE", "RELEASE", (750, 700))
        area = canvas.board_area(board)
        area.spaces.active.show_region_ui = True
    elif step == 4:
        area = canvas.board_area(board)
        region = next(item for item in area.regions if item.type == "UI")
        if region.width < 50:
            return .2
        region.active_panel_category = "Remi"
    elif step == 5:
        result = capture("sidebar-no-atlas.png")
        region = canvas.window_region(canvas.board_area(board))
        # The otherwise transparent part below the native N-panel must not
        # expose the host Image Editor's atlas (or the preceding draw handler).
        x = region.x+board.region[0]+70
        sampled = result[region.y+240:region.y+280, x:x+40, :3]
        assert np.max(np.ptp(sampled, axis=(0, 1))) < .035, "Native image leaked through the sidebar gutter"
        assert sampled.max() < .5, "Native image-editor atlas remained behind the board"
        assert canvas.board_area(board).spaces.active.image == image, "The fix should not delete or replace the user's image"
        assert np.allclose(np.array(image.pixels).reshape(128, 128, 4), atlas_pixels, atol=.005)
        bpy.types.SpaceImageEditor.draw_handler_remove(underlay, "WINDOW")
        underlay = None
        if installed:
            assert bpy.ops.preferences.addon_disable(module=package) == {"FINISHED"}
        else:
            remi.unregister()
        print(f"REMI_BOARD_RENDER_SMOKE_OK: {OUTPUT}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return .5


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "failed.png"))
        print(f"REMI_BOARD_RENDER_SMOKE_FAILED step {step}: {OUTPUT}", flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=.6)
