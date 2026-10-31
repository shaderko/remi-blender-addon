"""Matched unlit candidate renders from a diagnostic's frozen exchanges (Blender)."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Matrix

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--snapshot", type=Path, required=True)
parser.add_argument("--candidate", action="append", required=True, help="name=albedo.png")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--lit", action="store_true", help="Use matched Principled materials and each candidate's normal.png")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
if args.output.exists() and any(args.output.iterdir()):
    raise ValueError("Candidate render output must be empty")
args.output.mkdir(parents=True, exist_ok=True)
config = json.loads((args.snapshot / "diagnostic.json").read_text())
with np.load(args.snapshot / "mesh.npz", allow_pickle=False) as archive:
    positions, uvs, normals = archive["positions"], archive["uvs"], archive["normals"]
unique, inverse = np.unique(positions.reshape(-1, 3), axis=0, return_inverse=True)
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
data = bpy.data.meshes.new("Frozen diagnostic target")
data.from_pydata(unique.tolist(), [], inverse.reshape(-1, 3).tolist())
uv = data.uv_layers.new(name="DiagnosticUV")
uv.data.foreach_set("uv", uvs.ravel())
data.polygons.foreach_set("use_smooth", np.ones(len(data.polygons), bool))
data.normals_split_custom_set(normals.reshape(-1, 3).tolist())
obj = bpy.data.objects.new("Frozen diagnostic target", data)
bpy.context.scene.collection.objects.link(obj)
material = bpy.data.materials.new("Candidate · unlit color")
material.use_nodes = True
nodes, links = material.node_tree.nodes, material.node_tree.links
nodes.clear()
texture = nodes.new("ShaderNodeTexImage")
shader = nodes.new("ShaderNodeBsdfPrincipled" if args.lit else "ShaderNodeEmission")
output = nodes.new("ShaderNodeOutputMaterial")
links.new(texture.outputs["Color"], shader.inputs["Base Color" if args.lit else "Color"])
links.new(shader.outputs["BSDF" if args.lit else "Emission"], output.inputs["Surface"])
if args.lit:
    shader.inputs["Roughness"].default_value = 0.7
    normal_texture = nodes.new("ShaderNodeTexImage")
    normal_node = nodes.new("ShaderNodeNormalMap")
    normal_node.uv_map = uv.name
    links.new(normal_texture.outputs["Color"], normal_node.inputs["Color"])
    links.new(normal_node.outputs["Normal"], shader.inputs["Normal"])
data.materials.append(material)
camera_data = bpy.data.cameras.new("Matched camera")
camera_data.lens, camera_data.sensor_width, camera_data.sensor_fit = 50, 36, "HORIZONTAL"
camera = bpy.data.objects.new("Matched camera", camera_data)
scene = bpy.context.scene
scene.collection.objects.link(camera)
scene.camera = camera
scene.render.engine = "BLENDER_EEVEE"
scene.eevee.taa_render_samples = 32
scene.render.resolution_x = scene.render.resolution_y = config["resolution"]
scene.render.resolution_percentage = 100
scene.render.film_transparent = True
scene.render.use_compositing = False
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure, scene.view_settings.gamma = 0, 1
if args.lit:
    world = bpy.data.worlds.new("Matched studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.2, 0.2, 0.2, 1)
    scene.world = world
    for index, (location, energy) in enumerate((((1.5, -2, 2.0), 80), ((-2, 1.2, 1), 50))):
        light_data = bpy.data.lights.new(f"Studio {index}", "AREA")
        light_data.energy, light_data.shape, light_data.size = energy, "DISK", 3
        light = bpy.data.objects.new(light_data.name, light_data)
        scene.collection.objects.link(light)
        light.location = location
        light.rotation_euler = (-light.location).to_track_quat("-Z", "Y").to_euler()
for candidate in args.candidate:
    name, path = candidate.split("=", 1)
    if not name.replace("_", "").isalnum():
        raise ValueError("Use simple candidate names")
    image = bpy.data.images.load(str(Path(path).resolve()), check_existing=False)
    image.colorspace_settings.name = "sRGB"
    texture.image = image
    if args.lit:
        normal_path = Path(path).with_name("normal.png")
        normal_image = bpy.data.images.load(str(normal_path.resolve()), check_existing=False)
        normal_image.colorspace_settings.name = "Non-Color"
        normal_texture.image = normal_image
    for view in config["views"]:
        camera.matrix_world = Matrix((np.asarray(view["camera_to_world_opencv"]) @ np.diag([1, -1, -1, 1])).tolist())
        scene.render.filepath = str((args.output / f"{name}_{view['index']:02d}.png").resolve())
        bpy.context.view_layer.update()
        bpy.ops.render.render(write_still=True)
print("CANDIDATE_RENDERS_COMPLETE", args.output, flush=True)
