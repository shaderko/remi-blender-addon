"""Import a generated, vertex-colored surface into the current scene."""
from pathlib import Path

import bpy

from .gaussian_splats import is_splat_object


MESH_TAG = "remi_reconstructed_mesh"


def is_reconstructed_mesh(obj):
    return bool(obj and obj.get(MESH_TAG))


def mesh_for_splat(splat):
    """Return the reconstructed mesh currently associated with a splat."""
    if not is_splat_object(splat):
        return None
    linked = bpy.data.objects.get(splat.get("remi_mesh_object", ""))
    if is_reconstructed_mesh(linked):
        return linked
    # Older files and renamed objects: fall back to the source-object tag.
    for candidate in bpy.data.objects:
        if is_reconstructed_mesh(candidate) and candidate.get("remi_mesh_source_object") == splat.name:
            return candidate
    return None


def _vertex_color_material(name, layer_name):
    material = bpy.data.materials.new(name)
    try:
        material.use_nodes = True
        material.diffuse_color = (0.55, 0.58, 0.62, 1.0)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.clear()
        color = nodes.new("ShaderNodeVertexColor")
        color.layer_name = layer_name
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        shader.inputs["Roughness"].default_value = 0.7
        output = nodes.new("ShaderNodeOutputMaterial")
        if layer_name:
            links.new(color.outputs["Color"], shader.inputs["Base Color"])
        else:
            shader.inputs["Base Color"].default_value = (0.55, 0.58, 0.62, 1.0)
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
        return material
    except Exception:
        bpy.data.materials.remove(material)
        raise


def _place_with_source(obj, source_object):
    """Keep the result in the same collection/scene as its Gaussian source."""
    if source_object is None or not source_object.users_collection:
        return
    target = source_object.users_collection[0]
    if target not in obj.users_collection:
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        target.objects.link(obj)


def import_reconstructed_mesh(context, path, *, source_object=None, report=None, source_matrix=None):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"Mesh file not found: {path}")
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before importing a mesh")
    if source_object is not None and not is_splat_object(source_object):
        raise ValueError("The mesh source must be a Remi Gaussian splat object")
    previous = set(bpy.data.objects)
    meshes_before = set(bpy.data.meshes)
    materials_before = set(bpy.data.materials)
    active = context.view_layer.objects.active
    material = None
    try:
        bpy.ops.wm.ply_import(filepath=str(path))
        imported = [obj for obj in bpy.data.objects if obj not in previous]
        meshes = [obj for obj in imported if obj.type == "MESH" and len(obj.data.polygons)]
        if len(meshes) != 1:
            for obj in imported:
                bpy.data.objects.remove(obj, do_unlink=True)
            raise ValueError("Meshed PLY did not produce exactly one non-empty mesh")
        obj = meshes[0]
        if source_object is not None:
            obj.matrix_world = source_object.matrix_world.copy()
        elif source_matrix is not None:
            obj.matrix_world = source_matrix
        # Smooth shading does not alter dense reconstruction geometry.
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
        layer = next(iter(obj.data.color_attributes), None)
        material = _vertex_color_material(
            f"{path.parent.name} · reconstructed", layer.name if layer else "",
        )
        obj.data.materials.clear()
        obj.data.materials.append(material)
        label = source_object.name if source_object is not None else path.parent.name
        obj.name = f"Remi Mesh · {label}"
        obj[MESH_TAG] = 1
        obj["remi_mesh_schema"] = 1
        obj["remi_mesh_ply"] = str(path)
        obj["remi_mesh_report"] = str(report or path.with_name("report.json"))
        if source_object is not None:
            obj["remi_mesh_source_object"] = source_object.name
            obj["remi_splat_source"] = source_object
            archive = source_object.get("remi_splat_archive")
            if archive:
                obj["remi_mesh_source_archive"] = str(archive)
        _place_with_source(obj, source_object)
        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        # Keep the source splat in the scene, but hide only this source object.
        if source_object is not None:
            source_object.hide_set(True)
            source_object["remi_mesh_object"] = obj.name
            source_object["remi_mesh_ply"] = str(path)
            source_object["remi_mesh_report"] = obj["remi_mesh_report"]
            source_object["remi_mesh_triangles"] = len(obj.data.polygons)
        return obj
    except Exception:
        for obj in [o for o in bpy.data.objects if o not in previous]:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in [m for m in bpy.data.meshes if m not in meshes_before and m.users == 0]:
            bpy.data.meshes.remove(mesh)
        for candidate in [m for m in bpy.data.materials if m not in materials_before and m.users == 0]:
            bpy.data.materials.remove(candidate)
        if material is not None and material.users == 0:
            bpy.data.materials.remove(material)
        context.view_layer.objects.active = active
        raise
