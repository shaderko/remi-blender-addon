"""Real-window board draw/orbit profiling. Optional -- <splats.npz>, no inference."""

import cProfile
from datetime import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import pstats
import statistics
import sys
import tempfile
import time
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_board_perf_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
OUTPUT.mkdir(parents=True)
os.environ["REMI_BOARD_LAYOUT"] = str(OUTPUT / "layout.json")
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
from remi.features.board import canvas
from remi.features.board.assets import Asset

image = bpy.data.images.new("Performance fixture", 64, 64)
image.generated_color = (0.12, 0.35, 0.65, 1)
image.filepath_raw = str(OUTPUT / "thumbnail.png")
image.file_format = "PNG"
image.save()
bpy.data.images.remove(image)
bpy.context.scene.remi_image.output = str(OUTPUT / "images")
bpy.context.scene.remi_generation.output = str(OUTPUT / "models")
original_draw = canvas.board_draw
samples = []
profile = cProfile.Profile()
step = 0


def measured_draw():
    if step != 2:
        return original_draw()
    start = time.perf_counter()
    if len(samples) in {5, 6, 7, 8, 9}:
        profile.enable()
        try:
            original_draw()
        finally:
            profile.disable()
    else:
        original_draw()
    samples.append(time.perf_counter()-start)


canvas.board_draw = measured_draw


def advance():
    global step
    try:
        if step == 0:
            bpy.ops.remi_board.open()
        elif step == 1:
            board = canvas.BOARD
            if board is None:
                return 0.1
            for index in range(120):
                identifier = f"perf:{index}"
                asset = Asset(identifier, "IMAGE", artifact=str(OUTPUT / "thumbnail.png"),
                              title="A detailed blue bowl with a fluted ceramic surface",
                              parent=f"perf:{index-1}" if index % 2 else "")
                board.catalog.assets[identifier] = asset
                board.order.append(identifier)
                board.positions[identifier] = [(index % 12)*330, -(index//12)*330]
                board.load_texture(identifier)
            board.selected = {"perf:0"}
            board.fit()
        elif step == 2:
            if len(samples) < 25:
                canvas.board_area(canvas.BOARD).tag_redraw()
                return 0.02
            text = io.StringIO()
            pstats.Stats(profile, stream=text).sort_stats("cumtime").print_stats(25)
            (OUTPUT / "draw_profile.txt").write_text(text.getvalue(), encoding="utf-8")
            print(text.getvalue(), flush=True)
            report = {"draw_median_ms": statistics.median(samples[10:])*1000,
                      "draw_max_ms": max(samples[10:])*1000}
            if "--" in sys.argv:
                from remi.integrations.triposplat.splat_data import load
                arrays = load(Path(sys.argv[sys.argv.index("--")+1]))
                durations = []
                for index in range(12):
                    start = time.perf_counter()
                    preview = canvas.Preview(arrays, yaw=index*3, elevation=20)
                    preview.free()
                    durations.append(time.perf_counter()-start)
                report["orbit_rebuild_median_ms"] = statistics.median(durations[2:])*1000
                preview = canvas.NativePreview(arrays, elevation=20)
                try:
                    durations = []
                    for index in range(32):
                        start = time.perf_counter()
                        preview.render(yaw=index*3, elevation=20)
                        durations.append(time.perf_counter()-start)
                    report["orbit_reuse_median_ms"] = statistics.median(durations[2:])*1000
                    report["orbit_reuse_p95_ms"] = sorted(durations[2:])[28]*1000
                finally:
                    preview.free()
            (OUTPUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("REMI_BOARD_PERFORMANCE", json.dumps(report), str(OUTPUT), flush=True)
            remi.unregister()
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 0.1
    except Exception:
        traceback.print_exc()
        os._exit(1)


bpy.app.timers.register(advance, first_interval=0.5)
