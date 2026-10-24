"""Render turntable previews of a reconstructed mesh for visual comparison."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--mesh", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--views", type=int, default=4)
parser.add_argument("--resolution", type=int, default=512)
parser.add_argument("--clay", action="store_true", help="ignore mesh colors")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
mesh_path = args.mesh.resolve()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
bpy.ops.wm.ply_import(filepath=str(mesh_path))
meshes = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
if not meshes:
    raise RuntimeError("No mesh imported")
obj = meshes[0]
bpy.context.view_layer.objects.active = obj
obj.select_set(True)

# Imported PLY vertex colors are commonly stored as a color attribute.
color_attribute = None
for attribute in obj.data.color_attributes:
    color_attribute = attribute.name
    break

material = bpy.data.materials.new("Reconstruction preview")
material.use_nodes = True
nodes = material.node_tree.nodes
links = material.node_tree.links
nodes.clear()
output_node = nodes.new("ShaderNodeOutputMaterial")
shader = nodes.new("ShaderNodeBsdfPrincipled")
shader.inputs["Roughness"].default_value = 0.7
shader.inputs["Base Color"].default_value = (0.55, 0.58, 0.62, 1.0)
links.new(shader.outputs["BSDF"], output_node.inputs["Surface"])
if color_attribute and not args.clay:
    color = nodes.new("ShaderNodeVertexColor")
    color.layer_name = color_attribute
    links.new(color.outputs["Color"], shader.inputs["Base Color"])
obj.data.materials.clear()
obj.data.materials.append(material)

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = args.resolution
scene.render.resolution_percentage = 100
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.color_depth = "8"
scene.render.use_file_extension = False
scene.world.color = (0.05, 0.05, 0.05)
scene.view_settings.view_transform = "Standard"

# Bounding box in object space (the reconstruction is already in world space).
corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
low = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
high = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
center = (low + high) / 2
extent = max((high - low).x, (high - low).y, (high - low).z)

camera_data = bpy.data.cameras.new("Preview Camera")
camera = bpy.data.objects.new("Preview Camera", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera
camera_data.type = "ORTHO"
camera_data.ortho_scale = extent * 1.25

sun_data = bpy.data.lights.new("Preview Sun", type="SUN")
sun_data.energy = 3.0
sun = bpy.data.objects.new("Preview Sun", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(25), math.radians(15), math.radians(30))

scene.render.filepath = str(output / "preview")
for index in range(args.views):
    angle = 2 * math.pi * index / args.views
    camera.location = center + Vector((math.sin(angle), -math.cos(angle), 0.0)) * extent * 3.0
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(output / f"view_{index:02d}.png")
    bpy.ops.render.render(write_still=True)
    print(f"Rendered preview {index + 1}/{args.views}", flush=True)
bpy.ops.wm.save_as_mainfile(filepath=str(output / "preview.blend"))
print(f"Preview written to {output}", flush=True)
