"""UV/material-only MeshLab exchange, independent of artist image datablocks.

The texture quadric operates on UVs, not image pixels. Give each material a
small, uniquely named proxy texture; restore the actual materials afterwards.
Never round-trip a Principled graph (or a normal map) through OBJ/MTL.
"""

from contextlib import contextmanager
from pathlib import Path
import math
import struct
import zlib

import bpy

from ...blender import mesh_exchange


def validate_uv(mesh):
    layer = mesh.uv_layers.active
    if layer is None:
        raise ValueError("Keep Texture needs an active UV map; no UVs will be generated during reduction")
    if any(not math.isfinite(value) for loop in layer.data for value in loop.uv):
        raise ValueError("The active UV map contains non-finite coordinates")


def _white_png(path):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" +
        chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) +
        chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff")) + chunk(b"IEND", b""))


@contextmanager
def export_uv_mesh(obj, path):
    """Export an evaluated disposable copy; yield materials and UV channel name."""
    context = bpy.context
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    mesh, part = None, None
    materials, images = [], []
    try:
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        validate_uv(mesh)
        # OBJ has only one UV channel. Use the same channel that Blender renders.
        render_uv = next((layer for layer in mesh.uv_layers if layer.active_render), mesh.uv_layers.active)
        mesh.uv_layers.active = render_uv
        validate_uv(mesh)
        originals = [slot.material.original if slot.material else None
                     for slot in evaluated.material_slots] or [None]
        indices = [min(face.material_index, len(originals)-1) for face in mesh.polygons]
        mesh.materials.clear()
        for index in range(len(originals)):
            name = f"remi_slot_{index:04d}"
            image_path = Path(path).parent / (name + ".png")
            _white_png(image_path)
            image = bpy.data.images.load(str(image_path), check_existing=False)
            images.append(image)
            material = bpy.data.materials.new(name)
            materials.append(material)
            material.use_nodes = True
            node = material.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            material.node_tree.links.new(node.outputs["Color"],
                material.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
            mesh.materials.append(material)
        mesh.polygons.foreach_set("material_index", indices)
        part = bpy.data.objects.new("Remi UV exchange", mesh)
        context.collection.objects.link(part)
        part.matrix_world = obj.matrix_world.copy()
        if not mesh_exchange.export_obj_for_tool(part, str(path), export_materials=True, triangulate=True):
            raise RuntimeError("UV-preserving OBJ export failed")
        mtl = Path(path).with_suffix(".mtl")
        # VCG's MTL parser mishandles map paths containing spaces. All proxy
        # files live beside the OBJ, so a simple relative basename is enough.
        lines = mtl.read_text(encoding="utf-8").splitlines()
        lines = [("map_Kd " + line.rsplit("/", 1)[-1]) if line.startswith("map_Kd ") else line
                 for line in lines]
        mtl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        yield {"materials": originals, "uv_name": render_uv.name}
    finally:
        if part is not None:
            bpy.data.objects.remove(part, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for material in materials:
            bpy.data.materials.remove(material)
        for image in images:
            bpy.data.images.remove(image)
        for other in context.selected_objects:
            other.select_set(False)
        for other in selected:
            if other.name in context.view_layer.objects:
                other.select_set(True)
        context.view_layer.objects.active = active


def restore_materials(result, originals):
    """Use proxy texture filenames, not MeshLab's reordered material names."""
    mapping = {}
    for slot_index, material in enumerate(result.data.materials):
        if material is None or material.node_tree is None:
            raise RuntimeError("MeshLab lost a material's UV texture index")
        names = [Path(node.image.filepath).stem for node in material.node_tree.nodes
                 if node.type == "TEX_IMAGE" and node.image is not None]
        indices = [int(name[len("remi_slot_"):]) for name in names
                   if name.startswith("remi_slot_") and name[len("remi_slot_"):].isdigit()]
        if len(indices) != 1 or not 0 <= indices[0] < len(originals):
            raise RuntimeError("MeshLab returned an unrecognized UV material; source was kept")
        mapping[slot_index] = indices[0]
    indices = [mapping[face.material_index] for face in result.data.polygons]
    temporary_materials = list(result.data.materials)
    temporary_images = {node.image for material in temporary_materials if material and material.node_tree
                        for node in material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image}
    result.data.materials.clear()
    for material in originals:
        result.data.materials.append(material)
    result.data.polygons.foreach_set("material_index", indices)
    for material in temporary_materials:
        if material is not None and material.users == 0:
            bpy.data.materials.remove(material)
    for image in temporary_images:
        if image.users == 0:
            bpy.data.images.remove(image)
