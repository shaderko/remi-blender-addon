"""Real-window source rebuild controls, measured density and embedded accept/Back."""

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
from mathutils import Euler
import numpy as np

if os.environ.get("REMI_TEST_DEPENDENCIES"):
    sys.path.insert(0, os.environ["REMI_TEST_DEPENDENCIES"])

ROOT = Path(__file__).resolve().parents[1]
exec(compile((ROOT / "tests/blender_checkout_bootstrap.py").read_text(),
             str(ROOT / "tests/blender_checkout_bootstrap.py"), "exec"))
import remi
remi.register()
from remi.features.retopology.instant_meshes.runtime import runtime
from remi.app.application import get_application
from remi.workflow import session

sys.path.insert(0, str(ROOT / "tests"))
from test_instant_meshes_adaptive import grid, HeightSurface

OUTPUT = Path(tempfile.gettempdir()) / "opencode" / ("remi_surface_ui_"+datetime.now().strftime("%Y%m%d_%H%M%S"))
OUTPUT.mkdir(parents=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
vertices, faces = grid(49, HeightSurface.height)
ASSET_CACHE = os.environ.get("REMI_SURFACE_ASSET_CACHE")
if ASSET_CACHE:
    with np.load(ASSET_CACHE, allow_pickle=False) as data:
        vertices, faces = data["vertices"].copy(), data["faces"].copy()
mesh = bpy.data.meshes.new("Smooth versus detail")
mesh.from_pydata(vertices.tolist(), [], faces.tolist())
source = bpy.data.objects.new("Smooth versus detail", mesh)
bpy.context.collection.objects.link(source)
source.select_set(True)
bpy.context.view_layer.objects.active = source
settings = bpy.context.scene.remi_instant_meshes
settings.target_faces = 1400
settings.deterministic = True
settings.preserve_creases = True
settings.adaptive_quads = True
settings.adaptive_method = "SURFACE"
settings.adaptive_error = .01
settings.show_orientation = settings.show_singularities = False
settings.show_quality = True
settings.preview_offset = .4
settings.preview_fill_opacity = .04
area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
region = next(region for region in area.regions if region.type == "WINDOW")
area.spaces.active.region_3d.view_rotation = Euler((.5, 0, .5)).to_quaternion()
with bpy.context.temp_override(area=area, region=region):
    bpy.ops.view3d.view_selected()
step = waits = 0


def advance():
    global step, waits
    waits += 1
    assert waits < 600, f"Timed out at step {step}: {runtime.last_error}"
    if step == 0:
        bpy.context.window.event_simulate(type="ESC", value="PRESS")
        bpy.context.window.event_simulate(type="ESC", value="RELEASE")
    elif step == 1:
        session.runtime.begin(bpy.context, source)
        session.runtime.start_interactive_action(bpy.context, get_application().features.require_action("INSTANT_START"))
    elif step == 2:
        if runtime.busy or runtime.solve_stage:
            return .2
        assert runtime.adaptive_report["rebuilt"], runtime.adaptive_report
        assert runtime.adaptive_report["backend"] == "FIELD_ALIGNED", runtime.adaptive_report
        assert runtime.adaptive_report["ideal_valence_fraction"] >= .9
        assert runtime.adaptive_report["poles"] <= runtime.adaptive_report["output_faces"]*.05
        assert runtime.preview_quality["quad_fraction"] == 1
        assert runtime.preview_quality["components"] == 1
        assert runtime.preview_quality["nonmanifold_edges"] == 0
        assert runtime.preview_quality["boundary_edges"] > 0  # This fixture is an open sheet.
        (OUTPUT / "report.json").write_text(json.dumps(runtime.adaptive_report, indent=2), encoding="utf-8")
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "surface-density.png"))
        bpy.context.window_manager.remi_session.stage = "RETOPOLOGY"
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.wm.call_panel("INVOKE_DEFAULT", name="Remi_PT_MainPanel", keep_open=True)
    elif step == 3:
        bpy.ops.screen.screenshot(filepath=str(OUTPUT / "surface-settings.png"))
        bpy.context.window.event_simulate(type="ESC", value="PRESS")
        bpy.context.window.event_simulate(type="ESC", value="RELEASE")
    elif step == 4:
        count = len(runtime.preview[1])
        assert bpy.ops.remi.instant_meshes_session_accept() == {"FINISHED"}
        result = session.runtime.object(bpy.context)
        assert len(result.data.polygons) == count
        assert all(len(p.vertices) == 4 for p in result.data.polygons)
        session.runtime.undo(bpy.context)
        restored = session.runtime.object(bpy.context)
        np.testing.assert_array_equal(np.array([v.co[:] for v in restored.data.vertices], np.float32), vertices)
        session.runtime.cancel(bpy.context)
        remi.unregister()
        print("REMI_SURFACE_ADAPTIVE_UI_SMOKE_OK", OUTPUT, flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return .6


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        print("REMI_SURFACE_ADAPTIVE_UI_SMOKE_FAILED", step, OUTPUT, flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=.6)
