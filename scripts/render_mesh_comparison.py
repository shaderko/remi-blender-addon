"""Render benchmark meshes from exactly the held-out splat cameras in Blender."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--methods", type=Path, required=True, help="JSON mapping of method names to PLY paths")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    import bpy
    from mathutils import Matrix
    import numpy as np

    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"Output is not empty: {args.output}")
    dataset = json.loads((args.views / "dataset.json").read_text())
    methods = json.loads(args.methods.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = dataset["samples"]
    scene.render.resolution_x, scene.render.resolution_y = dataset["resolution"]
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.use_compositing = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.use_file_extension = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    data = bpy.data.cameras.new("Held-out camera")
    data.lens, data.sensor_width = dataset["lens_mm"], dataset["sensor_width_mm"]
    data.sensor_fit = "HORIZONTAL"
    data.clip_start, data.clip_end = dataset["near"], dataset["far"]
    camera = bpy.data.objects.new("Held-out camera", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    flip = np.diag([1.0, -1.0, -1.0, 1.0])
    for method, mesh_path in methods.items():
        if not method or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in method):
            raise ValueError(f"Unsafe method name: {method}")
        bpy.ops.wm.ply_import(filepath=str(Path(mesh_path).resolve()))
        objects = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
        if len(objects) != 1:
            raise ValueError(f"Expected one mesh: {mesh_path}")
        obj = objects[0]
        material = bpy.data.materials.new(f"Benchmark {method}")
        material.use_nodes = True
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.clear()
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1)
        if obj.data.color_attributes:
            color = nodes.new("ShaderNodeVertexColor")
            color.layer_name = obj.data.color_attributes[0].name
            links.new(color.outputs["Color"], emission.inputs["Color"])
        output = nodes.new("ShaderNodeOutputMaterial")
        links.new(emission.outputs[0], output.inputs["Surface"])
        obj.data.materials.clear()
        obj.data.materials.append(material)
        directory = args.output / method
        directory.mkdir()
        for view in dataset["evaluation_views"]:
            camera.matrix_world = Matrix((np.asarray(view["camera_to_world_opencv"]) @ flip).tolist())
            bpy.context.view_layer.update()
            scene.render.filepath = str(directory / f"view_{view['index']:04d}.png")
            bpy.ops.render.render(write_still=True)
        mesh_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh_data)
        bpy.data.materials.remove(material)
    (args.output / "render_complete.json").write_text(json.dumps({"complete": True}), encoding="utf-8")


if __name__ == "__main__":
    main()
