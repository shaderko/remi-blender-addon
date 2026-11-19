"""Real-window wireframe screenshots (front + close-up); saves nothing else."""
import os
from pathlib import Path
import traceback

import bpy
import numpy as np
from mathutils import Vector

OUTPUT = Path(os.environ.get("REMI_TOPOLOGY_OUTPUT", r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-topology-visual2"))
ROOT = Path(__file__).resolve().parents[1]
exec(compile((ROOT / "tests/blender_checkout_bootstrap.py").read_text(),
             str(ROOT / "tests/blender_checkout_bootstrap.py"), "exec"))
import remi
remi.register()
OUTPUT.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

MESHES = [
    ("current", Path(r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-untitled-worker-integration\mesh-0-0.005.npz")),
    ("native", Path(r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-topology-baseline\native-9600.npz")),
    ("coarse", Path(r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-topology-baseline\native-1200.npz")),
    ("source", Path(r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-untitled-diagnosis\source-0.npz")),
]
objects = {}
for name, path in MESHES:
    data = np.load(path)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(data["vertices"].tolist(), [], data["faces"].tolist())
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    material = bpy.data.materials.new(name + " surface")
    material.diffuse_color = (.55, .55, .58, 1) if name == "source" else (.78, .78, .80, 1)
    obj.data.materials.append(material)
    obj.data.polygons.foreach_set("use_smooth", [True] * len(obj.data.polygons))
    if name != "source":
        modifier = obj.modifiers.new("Wire", "WIREFRAME")
        modifier.thickness = .0016
        modifier.use_replace = False
        modifier.use_even_offset = True
        wire = bpy.data.materials.new(name + " wire")
        wire.diffuse_color = (.01, .01, .01, 1)
        obj.data.materials.append(wire)
    objects[name] = obj

area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
region = next(region for region in area.regions if region.type == "WINDOW")
space = area.spaces.active
space.shading.type = "SOLID"
space.shading.light = "STUDIO"
space.shading.color_type = "MATERIAL"
space.shading.show_cavity = True
space.shading.cavity_type = "BOTH"
space.overlay.show_floor = False
space.overlay.show_axis_x = space.overlay.show_axis_y = False
space.overlay.show_cursor = False

VIEW = Vector((1, 0, 0)).to_track_quat("-Z", "Y")
CLOSE_VIEW = Vector((1, .35, .25)).to_track_quat("-Z", "Y")
step = 0
waits = 0


def screenshot(name):
    bpy.ops.screen.screenshot(filepath=str(OUTPUT / (name + ".png")))


def show(name):
    for key, obj in objects.items():
        obj.hide_set(key != name)
    bpy.context.view_layer.objects.active = objects[name]


def frame(close=False):
    bpy.context.view_layer.update()
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.view3d.view_selected(use_all_regions=False)
    region_3d = space.region_3d
    region_3d.view_perspective = "ORTHO"
    region_3d.view_rotation = CLOSE_VIEW if close else VIEW
    if close:
        region_3d.view_distance *= .22


def advance():
    global step, waits
    waits += 1
    assert waits < 300, step
    if step == 0:
        bpy.context.window.event_simulate(type="ESC", value="PRESS")
        bpy.context.window.event_simulate(type="ESC", value="RELEASE")
    elif step == 1:
        show("source")
        frame()
        screenshot("source-front")
    elif step == 2:
        show("native")
        frame()
        screenshot("native-front")
    elif step == 3:
        frame(close=True)
        screenshot("native-close")
    elif step == 4:
        show("current")
        frame()
        screenshot("current-front")
    elif step == 5:
        frame(close=True)
        screenshot("current-close")
    elif step == 6:
        show("coarse")
        frame()
        screenshot("coarse-front")
    elif step == 7:
        print("REMI_TOPOLOGY_VISUAL_OK", OUTPUT, flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    waits = 0
    return .8


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        (OUTPUT / "failed.txt").write_text(traceback.format_exc())
        print("REMI_TOPOLOGY_VISUAL_FAILED", step, OUTPUT, flush=True)
        os._exit(1)

bpy.app.timers.register(guarded, first_interval=.8)
