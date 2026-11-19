"""Native mesh snapshots for board operations; GLB is a separate delivery format.

glTF stores triangles and attribute-split vertices, so it cannot be the source
of truth for editable quad topology. Snapshots contain evaluated, independent
mesh parts and packed material images, never the owning board/edit scene.
"""

from pathlib import Path
import uuid

import bpy
import numpy as np


COLLECTION = "Remi Native Mesh Asset"
SCHEMA_KEY = "remi_native_mesh_schema"
DATA = ("objects", "meshes", "materials", "images", "node_groups", "textures", "collections",
        "armatures", "actions", "shape_keys", "cameras", "lights", "curves")


def _snapshot():
    return {name: set(getattr(bpy.data, name)) for name in DATA}


def _discard_new(before):
    created = [item for name in DATA for item in set(getattr(bpy.data, name))-before[name]]
    if created:
        bpy.data.batch_remove(ids=created)


def write(context, objects, path):
    """Bake evaluated geometry without triangulation, touching no source IDs."""
    path = Path(path)
    objects = [obj for obj in objects if obj.type == "MESH"]
    if not objects:
        raise ValueError("The native asset contains no mesh")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem+"."+uuid.uuid4().hex+".blend")
    before = _snapshot()
    try:
        collection = bpy.data.collections.new(COLLECTION+":"+uuid.uuid4().hex)
        collection[SCHEMA_KEY] = 1
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        object_copies, material_copies, image_copies, group_copies = {}, {}, {}, {}
        for obj in objects:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
            part = bpy.data.objects.new(obj.name+" · asset", mesh)
            part.matrix_world = evaluated.matrix_world.copy()
            collection.objects.link(part)
            object_copies[obj] = part

        def copy_image(image):
            if image not in image_copies:
                width, height = image.size
                pixels = np.empty(len(image.pixels), np.float32)
                if not width or not height or len(pixels) != width*height*4:
                    raise ValueError(f"Image has no readable pixel data: {image.name}")
                image.pixels.foreach_get(pixels)
                copied = image.copy()
                copied.use_fake_user = False
                # Image.copy() alone is not a reliable snapshot of dirty paint
                # buffers. Preserve the current pixels, not an older disk file.
                copied.pixels.foreach_set(pixels)
                copied.pack()
                image_copies[image] = copied
            return image_copies[image]

        def copy_tree(tree):
            if tree not in group_copies:
                copied = tree.copy()
                copied.use_fake_user = False
                group_copies[tree] = copied
                fix_nodes(copied)
            return group_copies[tree]

        def fix_nodes(tree):
            for node in tree.nodes:
                for prop in node.bl_rna.properties:
                    if prop.type != "POINTER" or prop.is_readonly:
                        continue
                    value = getattr(node, prop.identifier)
                    if not isinstance(value, bpy.types.ID):
                        continue
                    if isinstance(value, bpy.types.Image):
                        setattr(node, prop.identifier, copy_image(value))
                    elif isinstance(value, bpy.types.NodeTree):
                        setattr(node, prop.identifier, copy_tree(value))
                    elif isinstance(value, bpy.types.Object):
                        setattr(node, prop.identifier, object_copies.get(value))
                    else:
                        # Do not pull external scene/project ID dependencies
                        # into a geometry asset through a shader's pointers.
                        setattr(node, prop.identifier, None)

        for original, part in object_copies.items():
            mesh = part.data
            slots = [slot.material for slot in original.evaluated_get(depsgraph).material_slots]
            material_indices = [face.material_index for face in mesh.polygons]
            mesh.materials.clear()
            for material in slots:
                if material is not None and material not in material_copies:
                    copied = material.copy()
                    copied.use_fake_user = False
                    if copied.node_tree:
                        fix_nodes(copied.node_tree)
                    material_copies[material] = copied
                mesh.materials.append(material_copies.get(material))
            mesh.polygons.foreach_set("material_index", material_indices)
        bpy.data.libraries.write(str(temporary), {collection}, path_remap="ABSOLUTE", fake_user=True, compress=True)
        if not temporary.is_file() or temporary.stat().st_size < 20:
            raise RuntimeError("Could not save the native mesh snapshot")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        _discard_new(before)
    return path


def read(context, path):
    """Append fresh topology/material data; never weld or join separate parts."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("The native mesh snapshot is missing; restore its asset folder")
    before = _snapshot()
    try:
        with bpy.data.libraries.load(str(path), link=False) as (available, loaded):
            names = [name for name in available.collections if name == COLLECTION or name.startswith(COLLECTION+":")]
            if len(names) != 1:
                raise ValueError("Not a Remi native mesh snapshot")
            loaded.collections = names
        collection = loaded.collections[0]
        if collection.get(SCHEMA_KEY) != 1 or collection.children:
            raise ValueError("Unsupported native mesh snapshot")
        meshes = [obj for obj in collection.objects if obj.type == "MESH"]
        if not meshes:
            raise ValueError("The native mesh snapshot contains no mesh")
        for obj in list(context.selected_objects):
            obj.select_set(False)
        target = context.collection or context.scene.collection
        for obj in meshes:
            target.objects.link(obj)
            obj.hide_viewport = obj.hide_select = False
            obj.hide_set(False)
            obj.select_set(True)
        bpy.data.collections.remove(collection)
        context.view_layer.update()
        context.view_layer.objects.active = meshes[0]
        return meshes
    except Exception:
        _discard_new(before)
        raise
