"""Quantitative Gaussian rendering and UI alpha/color checks in a real window."""

import importlib.util
import os
from pathlib import Path
import sys
import traceback
import tempfile
import time

import bpy
import gpu
from mathutils import Matrix
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
from remi.features.board.splat_preview import Preview
from remi.features.board.canvas import draw_image


def read(offscreen, size=256):
    with offscreen.bind():
        data = gpu.state.active_framebuffer_get().read_color(0, 0, size, size, 4, 0, "FLOAT")
        return np.array(data, dtype=np.float32).reshape(size, size, 4)


def run():
    try:
        arrays = {
            "positions": np.zeros((1, 3), dtype=np.float32),
            "scales": np.array([[0.15, 0.03, 0.05]], dtype=np.float32),
            "rotations": np.array([[1, 0, 0, 0]], dtype=np.float32),
            "opacities": np.array([0.5], dtype=np.float32),
            "sh_dc": (np.array([[1, 0, 0]], dtype=np.float32)-0.5)/0.28209479177387814,
        }
        before = {name: set(getattr(bpy.data, name).keys()) for name in
                  ("scenes", "objects", "pointclouds", "cameras", "screens")}
        preview = Preview(arrays, size=256)
        assert before == {name: set(getattr(bpy.data, name).keys()) for name in before}, "Preview leaked scene data"
        try:
            pixels = read(preview.offscreen)
            print("REMI_PREVIEW_SAMPLES", pixels[128,128].tolist(), pixels[0,0].tolist(), pixels.max(axis=(0,1)).tolist(), flush=True)
            assert 0.47 < pixels[128,128,3] < 0.51, pixels[128,128]
            np.testing.assert_allclose(pixels[128,128,0], pixels[128,128,3], atol=0.002)
            assert pixels[0,0,3] < 1e-5
            assert pixels[128,148,3] > pixels[148,128,3] * 2, "Anisotropic scales were lost"
            target = gpu.types.GPUOffScreen(256, 256, format="RGBA16F")
            try:
                with target.bind(), gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                    gpu.state.viewport_set(0, 0, 256, 256)
                    gpu.state.active_framebuffer_get().clear(color=(0.1,0.1,0.1,1))
                    gpu.matrix.load_matrix(Matrix.Identity(4))
                    gpu.matrix.load_projection_matrix(Matrix(((2/256,0,0,-1),(0,2/256,0,-1),(0,0,-1,0),(0,0,0,1))))
                    draw_image(preview.texture, 0, 0, 256, 256, True)
                displayed = read(target)
                np.testing.assert_allclose(displayed[0,0,:3], 0.1, atol=0.005)
                assert 0.52 < displayed[128,128,0] < 0.57, displayed[128,128]
                # Mid-range RGB catches accidental double gamma conversion;
                # primary red and transparency alone cannot detect it.
                arrays["sh_dc"][:] = (0.5-0.5)/0.28209479177387814
                grey = Preview(arrays, size=256)
                try:
                    with target.bind(), gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                        gpu.state.viewport_set(0, 0, 256, 256)
                        gpu.state.active_framebuffer_get().clear(color=(0.1,0.1,0.1,1))
                        gpu.matrix.load_matrix(Matrix.Identity(4))
                        gpu.matrix.load_projection_matrix(Matrix(((2/256,0,0,-1),(0,2/256,0,-1),(0,0,-1,0),(0,0,0,1))))
                        draw_image(grey.texture, 0, 0, 256, 256, True)
                    np.testing.assert_allclose(read(target)[128,128,:3], 0.157, atol=0.005)
                    native_display = read(target)
                    with tempfile.TemporaryDirectory(prefix="remi-thumbnail-", dir=Path(tempfile.gettempdir()) / "opencode") as temporary:
                        thumbnail = grey.save(Path(temporary) / "thumbnail.png")
                        image = bpy.data.images.load(str(thumbnail), check_existing=False)
                        try:
                            texture = gpu.texture.from_image(image)
                            with target.bind(), gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                                gpu.state.viewport_set(0, 0, 256, 256)
                                gpu.state.active_framebuffer_get().clear(color=(0.1,0.1,0.1,1))
                                gpu.matrix.load_matrix(Matrix.Identity(4))
                                gpu.matrix.load_projection_matrix(Matrix(((2/256,0,0,-1),(0,2/256,0,-1),(0,0,-1,0),(0,0,0,1))))
                                draw_image(texture, 0, 0, 256, 256, False)
                            np.testing.assert_allclose(read(target), native_display, atol=0.008,
                                                       err_msg="Captured thumbnail changed color/alpha")
                        finally:
                            bpy.data.images.remove(image)
                finally:
                    grey.free()
            finally:
                target.free()
        finally:
            preview.free()
        # Depth ordering: an opaque red Gaussian in front must obscure blue.
        arrays = {key: np.repeat(value, 2, axis=0) for key, value in arrays.items()}
        arrays["positions"][:,1] = (-0.1, 0.1)
        arrays["opacities"][:] = 1
        arrays["sh_dc"] = (np.array([[1,0,0],[0,0,1]], dtype=np.float32)-0.5)/0.28209479177387814
        preview = Preview(arrays, size=256)
        try:
            pixels = read(preview.offscreen)
            assert pixels[128,128,0] > pixels[128,128,2]*20, pixels[128,128]
        finally:
            preview.free()
        if "--" in sys.argv:
            # Optional real-data quality/performance check, never writes into
            # the source generation folder or installs inference dependencies.
            from remi.integrations.triposplat.splat_data import load
            source = Path(sys.argv[sys.argv.index("--")+1])
            data = load(source)
            started = time.perf_counter()
            preview = Preview(data, size=512, elevation=20)
            try:
                pixels = read(preview.offscreen, 512)
                assert np.isfinite(pixels).all() and pixels[:,:,3].max() > 0.1
                image = bpy.data.images.new("Real Gaussian preview test", 512, 512, alpha=True, float_buffer=True)
                image.alpha_mode = "PREMUL"
                image.pixels.foreach_set(pixels.astype(np.float32).ravel())
                output = Path(tempfile.gettempdir()) / "opencode" / "remi_real_gaussian_preview.png"
                image.filepath_raw = str(output)
                image.file_format = "PNG"
                image.save()
                bpy.data.images.remove(image)
                print(f"REMI_REAL_PREVIEW_OK: {len(data['positions']):,} Gaussians, {time.perf_counter()-started:.3f}s, {output}", flush=True)
            finally:
                preview.free()
        print("REMI_BOARD_PREVIEW_SMOKE_OK", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    except Exception:
        traceback.print_exc()
        os._exit(1)


bpy.app.timers.register(run, first_interval=0.5)
