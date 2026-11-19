"""Orthographic EEVEE wireframe renders for topology review."""
import os
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector

OUTPUT = Path(os.environ.get("REMI_TOPOLOGY_OUTPUT", r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-topology-render"))
OUTPUT.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

MESHES = [
    ("current", Path(r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-untitled-worker-integration\mesh-0-0.005.npz")),
    ("field", Path(r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-field-route\mesh-0-0.01.npz")),
    ("native", Path(r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-topology-baseline\native-9600.npz")),
    ("coarse", Path(r"C:\Users\skaredko\AppData\Local\Temp\opencode\remi-topology-baseline\native-1200.npz")),
]
for name, path in MESHES:
    data = np.load(path)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(data["vertices"].tolist(), [], data["faces"].tolist())
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    surface = bpy.data.materials.new(name+" surface")
    surface.use_nodes = True
    nodes = surface.node_tree.nodes
    nodes.clear()
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs[0].default_value = (.72, .72, .74, 1)
    output = nodes.new("ShaderNodeOutputMaterial")
    surface.node_tree.links.new(emission.outputs[0], output.inputs[0])
    obj.data.materials.append(surface)
    wire = obj.modifiers.new("Wire", "WIREFRAME")
    wire.thickness = .0012
    wire.use_replace = False
    wire.use_even_offset = True
    wire.material_offset = 1
    black = bpy.data.materials.new(name+" wire")
    black.use_nodes = True
    black.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (.005, .005, .005, 1)
    black.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1
    obj.data.materials.append(black)
    obj.hide_render = True

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 1400
scene.render.film_transparent = False
world = bpy.data.worlds.new("White")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
scene.world = world
camera_data = bpy.data.cameras.new("Camera")
camera_data.type = "ORTHO"
camera = bpy.data.objects.new("Camera", camera_data)
bpy.context.collection.objects.link(camera)
scene.camera = camera
camera.rotation_euler = (np.pi/2, 0, np.pi/2)  # look along -X, Y up
light_data = bpy.data.lights.new("Sun", "SUN")
light_data.energy = 3
light = bpy.data.objects.new("Sun", light_data)
bpy.context.collection.objects.link(light)
light.rotation_euler = (.6, .2, .3)

for name, _path in MESHES:
    obj = bpy.data.objects[name]
    for other in bpy.data.objects:
        if other.type == "MESH":
            other.hide_render = other != obj
    for side, label, turn in ((4, "front", np.pi/2), (-4, "back", -np.pi/2)):
        camera.rotation_euler = (np.pi/2, 0, turn)
        camera.location = (side, 0, 0)
        camera_data.ortho_scale = 1.08
        scene.render.filepath = str(OUTPUT / f"{name}-{label}.png")
        bpy.ops.render.render(write_still=True)
        camera_data.ortho_scale = .34
        camera.location = (side, 0, .30)
        scene.render.filepath = str(OUTPUT / f"{name}-{label}-detail.png")
        bpy.ops.render.render(write_still=True)
print("REMI_TOPOLOGY_RENDER_OK", OUTPUT, flush=True)
