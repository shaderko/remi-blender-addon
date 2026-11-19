"""Real-window Full/Half/Quarter preview and embedded accept/Back/cancel."""

from datetime import datetime
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
import numpy as np
from mathutils import Euler

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(tempfile.gettempdir())/"opencode"/("remi_adaptive_ui_"+datetime.now().strftime("%Y%m%d_%H%M%S"))
OUTPUT.mkdir(parents=True)
spec = importlib.util.spec_from_file_location("remi", ROOT/"__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
from remi.features.retopology.instant_meshes.runtime import runtime
from remi.app.application import get_application
from remi.workflow import session

sys.path.insert(0, str(ROOT/"tests"))
from test_instant_meshes_adaptive import grid, HeightSurface

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
vertices, faces = grid(33, HeightSurface.height)
mesh = bpy.data.meshes.new("Adaptive detail test")
mesh.from_pydata(vertices.tolist(), [], faces.tolist())
source = bpy.data.objects.new("Adaptive detail test", mesh)
bpy.context.collection.objects.link(source)
source.select_set(True)
bpy.context.view_layer.objects.active = source
settings = bpy.context.scene.remi_instant_meshes
settings.target_faces = 1400
settings.deterministic = True
settings.preserve_creases = False
settings.show_orientation = False
settings.show_singularities = False
settings.preview_offset = .075
settings.preview_fill_opacity = .04
settings.show_quality = True
settings.adaptive_error = .003
area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
region = next(region for region in area.regions if region.type == "WINDOW")
area.spaces.active.region_3d.view_rotation = Euler((math.radians(35), 0, math.radians(35))).to_quaternion()
with bpy.context.temp_override(area=area, region=region):
    bpy.ops.view3d.view_selected()
step = waits = 0
native_count = half_count = None
reports = {}
dismissed = False


def advance():
    global step, waits, native_count, half_count
    waits += 1
    assert waits < 200, f"Timed out at step {step}: {runtime.last_error}"
    if step == 0:
        session.runtime.begin(bpy.context, source)
        session.runtime.start_interactive_action(bpy.context, get_application().features.require_action("INSTANT_START"))
    elif step == 1:
        if runtime.busy or runtime.solve_stage:
            return .2
        assert not runtime.last_error, runtime.last_error
        native_count = len(runtime.preview[1])
        assert native_count > 500
        bpy.ops.screen.screenshot(filepath=str(OUTPUT/"full.png"))
        settings.adaptive_quads = True
        settings.adaptive_method = "SIMPLIFY"
        settings.adaptive_budget = "0.5"
        runtime.update_preview()
    elif step == 2:
        if runtime.busy:
            return .2
        assert not runtime.last_error, runtime.last_error
        half_count = len(runtime.preview[1])
        assert half_count < native_count and runtime.adaptive_report["collapses"] > 0
        assert runtime.preview_quality["quad_fraction"] == 1
        reports["half"] = runtime.adaptive_report
        bpy.ops.screen.screenshot(filepath=str(OUTPUT/"half.png"))
        settings.adaptive_budget = "0.25"
        runtime.update_preview()
    elif step == 3:
        if runtime.busy:
            return .2
        assert len(runtime.preview[1]) <= half_count
        reports["quarter"] = runtime.adaptive_report
        bpy.ops.screen.screenshot(filepath=str(OUTPUT/"quarter.png"))
        bpy.context.window_manager.remi_session.stage = "RETOPOLOGY"
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.wm.call_panel("INVOKE_DEFAULT", name="Remi_PT_MainPanel", keep_open=True)
    elif step == 4:
        bpy.ops.screen.screenshot(filepath=str(OUTPUT/"adaptive-settings.png"))
        bpy.context.window.event_simulate(type="ESC", value="PRESS")
        bpy.context.window.event_simulate(type="ESC", value="RELEASE")
    elif step == 5:
        count = len(runtime.preview[1])
        assert bpy.ops.remi.instant_meshes_session_accept() == {"FINISHED"}
        result = session.runtime.object(bpy.context)
        assert len(result.data.polygons) == count and all(len(face.vertices) == 4 for face in result.data.polygons)
        assert len(bpy.context.scene.objects) == 1
        session.runtime.undo(bpy.context)
        restored = session.runtime.object(bpy.context)
        np.testing.assert_array_equal(np.array([v.co[:] for v in restored.data.vertices], np.float32), vertices)
        session.runtime.cancel(bpy.context)
        (OUTPUT/"report.json").write_text(json.dumps({"native_faces": native_count, "reports": reports}, indent=2), encoding="utf-8")
        remi.unregister()
        print("REMI_ADAPTIVE_UI_SMOKE_OK", OUTPUT, flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return .5


def guarded():
    global dismissed
    try:
        if not dismissed:
            # Normal Blender startup can leave the splash open; dismiss it as
            # a user would rather than changing or saving their preferences.
            bpy.context.window.event_simulate(type="ESC", value="PRESS")
            bpy.context.window.event_simulate(type="ESC", value="RELEASE")
            dismissed = True
            return .5
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT/"failed.txt").write_text(traceback.format_exc())
        bpy.ops.screen.screenshot(filepath=str(OUTPUT/"failed.png"))
        print("REMI_ADAPTIVE_UI_SMOKE_FAILED", step, OUTPUT, flush=True)
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=.6)
