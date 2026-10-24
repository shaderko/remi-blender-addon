"""Blender adapters for splat-to-texture projection.

The external worker never imports Blender. This module owns the mesh exchange,
image import, material creation, and the persistent appearance-source link.
"""
from __future__ import annotations

from pathlib import Path

import bpy
import numpy as np

from .gaussian_splats import is_splat_object


MASK_GROUP = "remi_projection_mask"


def write_world_splat_archive(source, path):
    """Write a splat archive in world space, matching the mesh exchange.

    The native archive stores object-local positions and rotations. Projection
    exports the mesh in world space, so a moved/rotated splat must be transformed
    once before the worker sees both inputs. Non-uniform scale is rejected
    because it would require re-decomposing every Gaussian covariance.
    """
    from ..integrations.triposplat.splat_data import save
    from .gaussian_splats import splat_arrays

    arrays = splat_arrays(source)
    matrix = source.matrix_world
    location, rotation, scale = matrix.decompose()
    if max(scale) - min(scale) > 1e-4 * max(max(scale), 1e-6):
        raise ValueError("Apply a uniform scale to the splat object before projecting textures")

    linear = np.asarray(matrix, dtype=np.float64)[:3, :3]
    translation = np.asarray(location, dtype=np.float64)
    positions = arrays["positions"].astype(np.float64) @ linear.T + translation
    scales = arrays["scales"].astype(np.float64) * float(scale[0])

    # Quaternion composition (world rotation * local rotation), WXYZ.
    qw, qx, qy, qz = (float(rotation.w), float(rotation.x), float(rotation.y), float(rotation.z))
    lw, lx, ly, lz = arrays["rotations"].T
    rotations = np.stack(
        (
            qw * lw - qx * lx - qy * ly - qz * lz,
            qw * lx + qx * lw + qy * lz - qz * ly,
            qw * ly - qx * lz + qy * lw + qz * lx,
            qw * lz + qx * ly - qy * lx + qz * lw,
        ),
        axis=1,
    )
    rotations /= np.maximum(np.linalg.norm(rotations, axis=1, keepdims=True), 1e-12)

    save(
        path,
        {
            "positions": positions.astype(np.float32),
            "scales": scales.astype(np.float32),
            "rotations": rotations.astype(np.float32),
            "opacities": arrays["opacities"],
            "sh_dc": arrays["sh_dc"],
        },
    )
    return Path(path)


def resolve_splat_source(mesh, manual=None):
    """Return ``(splat_object, archive_path)`` for a mesh's appearance source.

    Newly reconstructed meshes store an object pointer plus the older name and
    archive tags. Manual selection wins, so an artist can project a different
    splat onto an imported mesh.
    """
    if manual is not None:
        if not is_splat_object(manual):
            raise ValueError("Choose a Remi Gaussian splat object as the appearance source")
        return manual, None

    linked = mesh.get("remi_splat_source")
    if isinstance(linked, bpy.types.Object) and is_splat_object(linked):
        return linked, None

    name = mesh.get("remi_mesh_source_object")
    if name:
        candidate = bpy.data.objects.get(str(name))
        if is_splat_object(candidate):
            return candidate, None

    archive = mesh.get("remi_mesh_source_archive")
    if archive:
        path = Path(str(archive))
        if path.is_file():
            return None, path
    return None, None


def source_label(source_object, archive_path):
    if source_object is not None:
        return source_object.name
    if archive_path is not None:
        return Path(archive_path).parent.name or Path(archive_path).stem
    return ""


def ensure_texture_uv(obj, texture_size):
    """Return the active UV layer name, generating validated Remi UVs if needed."""
    if obj.type != "MESH":
        raise ValueError("Select a mesh object")
    if obj.data.uv_layers:
        from ..features.uv.engine.blender_bridge import validate_existing_uv

        stats = validate_existing_uv(obj)
        if stats is not None and stats.valid:
            return obj.data.uv_layers.active.name

    from ..features.uv.engine import ensure_remi_uv

    settings = bpy.context.scene.remi_settings
    result = ensure_remi_uv(
        obj,
        profile_id=settings.bake_uv_profile,
        texture_size=int(texture_size),
        margin_px=settings.bake_uv_margin_px,
        preserve_existing_seams=settings.bake_uv_preserve_seams,
        replace_existing=True,
        trust_stored_result=False,
    )
    if not result.success:
        raise RuntimeError(result.error or "Could not generate UVs for projection")
    if not obj.data.uv_layers:
        raise RuntimeError("UV generation completed without creating a UV map")
    return obj.data.uv_layers.active.name


def export_mesh_archive(context, obj, path):
    """Write per-corner world-space triangles, normals, and UVs for the worker."""
    if obj.type != "MESH":
        raise ValueError("Select a mesh object")
    if not obj.data.uv_layers:
        raise ValueError("The mesh has no UV map")
    path = Path(path)
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = None
    try:
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        mesh.calc_loop_triangles()
        if not len(mesh.loop_triangles):
            raise ValueError("The mesh has no triangles")
        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            raise ValueError("The evaluated mesh has no active UV map")

        triangle_count = len(mesh.loop_triangles)
        loop_count = len(mesh.loops)
        vertex_count = len(mesh.vertices)

        triangle_loops = np.empty(triangle_count * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", triangle_loops)
        triangle_vertices = np.empty(triangle_count * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", triangle_vertices)
        triangle_loops = triangle_loops.reshape(-1, 3)
        triangle_vertices = triangle_vertices.reshape(-1, 3)

        coordinates = np.empty(vertex_count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", coordinates)
        coordinates = coordinates.reshape(-1, 3)

        uv_values = np.empty(loop_count * 2, dtype=np.float32)
        uv_layer.data.foreach_get("uv", uv_values)
        uvs = uv_values.reshape(-1, 2)[triangle_loops]

        corner_normals = getattr(mesh, "corner_normals", None)
        if corner_normals is not None:
            normal_values = np.empty(loop_count * 3, dtype=np.float32)
            corner_normals.foreach_get("vector", normal_values)
            normals = normal_values.reshape(-1, 3)[triangle_loops]
        else:
            vertex_normals = np.empty(vertex_count * 3, dtype=np.float32)
            mesh.vertex_normals.foreach_get("vector", vertex_normals)
            normals = vertex_normals.reshape(-1, 3)[triangle_vertices]

        positions = coordinates[triangle_vertices].astype(np.float32)
        normals = normals.astype(np.float32)
        uvs = uvs.astype(np.float32)

        matrix = np.asarray(obj.matrix_world, dtype=np.float32)
        linear = matrix[:3, :3]
        translation = matrix[:3, 3]
        positions = positions @ linear.T + translation
        normal_matrix = np.linalg.inv(linear).T
        normals = normals @ normal_matrix.T

        # Degenerate or missing corner normals fall back to the face normal.
        lengths = np.linalg.norm(normals, axis=-1, keepdims=True)
        face = np.cross(positions[:, 1] - positions[:, 0], positions[:, 2] - positions[:, 0])
        face = face / np.maximum(np.linalg.norm(face, axis=1, keepdims=True), 1e-8)
        normals = np.where(lengths > 1e-8, normals / np.maximum(lengths, 1e-8), face[:, None, :])

        if not np.isfinite(positions).all() or not np.isfinite(normals).all():
            raise ValueError("Mesh exchange contains non-finite geometry")
        if not np.isfinite(uvs).all():
            raise ValueError("Mesh exchange contains non-finite UVs")

        mask = None
        group = obj.vertex_groups.get(MASK_GROUP)
        if group is not None:
            if len(mesh.vertices) != len(obj.data.vertices):
                print(
                    "Remi: projection mask skipped because modifiers change vertex topology"
                )
            else:
                weights = np.zeros(len(obj.data.vertices), dtype=np.float32)
                for vertex in obj.data.vertices:
                    try:
                        weights[vertex.index] = group.weight(vertex.index)
                    except RuntimeError:
                        pass
                mask = weights[triangle_vertices].astype(np.float32)

        payload = {
            "schema": np.array(1, dtype=np.int32),
            "positions": positions,
            "normals": normals.astype(np.float32),
            "uvs": uvs,
            "source_object": np.array(str(obj.name)),
            "triangle_count": np.array(triangle_count, dtype=np.int32),
        }
        if mask is not None:
            payload["mask"] = mask
        np.savez(path, **payload)
        return path
    finally:
        if mesh is not None:
            evaluated.to_mesh_clear()


def _load_image(name, path, colorspace, pack):
    path = str(path)
    if not Path(path).is_file():
        raise ValueError(f"Projected image not found: {path}")
    image = bpy.data.images.get(name)
    if image is None:
        image = bpy.data.images.load(path, check_existing=False)
        image.name = name
    else:
        image.filepath = path
        image.reload()
    image.colorspace_settings.name = colorspace
    image.file_format = "PNG"
    image.use_fake_user = True
    if pack:
        image.pack()
    return image


def _texture_node(nodes, name, image, location, uv_name):
    node = nodes.new("ShaderNodeTexImage")
    node.name = name
    node.label = name
    node.image = image
    node.location = location
    node.interpolation = "Linear"
    node.extension = "EXTEND"
    if uv_name and hasattr(node, "uv_map"):
        node.uv_map = uv_name
    return node


def existing_normal_map(mesh):
    """Return ``(image, uv_name)`` for a normal map already wired into the mesh."""
    for material in mesh.data.materials:
        if material is None or not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            normal_input = node.inputs.get("Normal")
            if normal_input is None or not normal_input.is_linked:
                continue
            source = normal_input.links[0].from_node
            uv_name = ""
            if source.type == "NORMAL_MAP":
                if hasattr(source, "uv_map"):
                    uv_name = source.uv_map
                color_input = source.inputs.get("Color")
                if color_input is None or not color_input.is_linked:
                    continue
                source = color_input.links[0].from_node
            if source.type == "TEX_IMAGE" and source.image is not None:
                if not uv_name and hasattr(source, "uv_map"):
                    uv_name = source.uv_map
                return source.image, uv_name
    return None, ""


def _projected_material(name, albedo, normal, uv_name):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name=name)
    material.use_nodes = True
    material.blend_method = "OPAQUE"
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (620, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (320, 0)
    bsdf.inputs["Roughness"].default_value = 0.7
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.0
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    albedo_node = _texture_node(nodes, "remi_projected_albedo", albedo, (-320, 180), uv_name)
    links.new(albedo_node.outputs["Color"], bsdf.inputs["Base Color"])

    if normal is not None:
        normal_node = _texture_node(nodes, "remi_projected_normal", normal, (-320, -140), uv_name)
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.location = (20, -140)
        if uv_name and hasattr(normal_map, "uv_map"):
            normal_map.uv_map = uv_name
        links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

    material.diffuse_color = (0.55, 0.58, 0.62, 1.0)
    return material


def _confidence_material(name, image, uv_name):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name=name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (360, 0)
    emission = nodes.new("ShaderNodeEmission")
    emission.location = (100, 0)
    emission.inputs["Strength"].default_value = 1.0
    texture = _texture_node(nodes, "remi_projected_confidence", image, (-260, 0), uv_name)
    links.new(texture.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    material.diffuse_color = (0.1, 0.1, 0.1, 1.0)
    return material


def _assign_material(mesh, material):
    mesh.data.materials.clear()
    mesh.data.materials.append(material)


def projection_available(mesh):
    return bool(
        mesh
        and (
            mesh.get("remi_texture_albedo")
            or mesh.get("remi_texture_material")
            or len(getattr(mesh, "remi_texture_layers", ()))
        )
    )


def projection_preview(mesh):
    return str(mesh.get("remi_texture_preview", "TEXTURE")) if mesh else "TEXTURE"


def apply_projection_material(mesh, mode="TEXTURE"):
    """Switch the mesh between the projected texture and its confidence view."""
    if not projection_available(mesh):
        raise ValueError("This mesh has no projected texture")
    key = "remi_texture_confidence_material" if mode == "CONFIDENCE" else "remi_texture_material"
    material = bpy.data.materials.get(str(mesh.get(key, "")))
    if material is None:
        raise ValueError("The projected material is missing")
    _assign_material(mesh, material)
    mesh["remi_texture_preview"] = "CONFIDENCE" if mode == "CONFIDENCE" else "TEXTURE"


def import_projection_result(
    mesh,
    paths,
    *,
    source_label_value="",
    pack_images=True,
    normal_mode="PROJECTED",
    uv_name=None,
):
    """Import worker images and build the projected base/confidence materials."""
    albedo_path = Path(paths["albedo"])
    normal_path = Path(paths["normal"])
    confidence_path = Path(paths["confidence"])
    if uv_name is None or mesh.data.uv_layers.get(uv_name) is None:
        uv_name = mesh.data.uv_layers.active.name if mesh.data.uv_layers else ""

    albedo = _load_image(
        f"{mesh.name} · projected albedo", albedo_path, "sRGB", pack_images
    )
    normal = _load_image(
        f"{mesh.name} · projected normal", normal_path, "Non-Color", pack_images
    )
    confidence = _load_image(
        f"{mesh.name} · projected confidence", confidence_path, "Non-Color", pack_images
    )

    normal_image = normal
    normal_uv = uv_name
    if normal_mode == "EXISTING":
        existing, existing_uv = existing_normal_map(mesh)
        # Do not mistake a previous projection's normal for an authored bake.
        if existing is not None and existing.name == f"{mesh.name} · projected normal":
            existing = None
        if existing is not None:
            existing.colorspace_settings.name = "Non-Color"
            normal_image = existing
            normal_uv = existing_uv or uv_name

    material = _projected_material(
        f"{mesh.name} · projected", albedo, normal_image, normal_uv
    )
    confidence_material = _confidence_material(
        f"{mesh.name} · projected confidence", confidence, uv_name
    )
    _assign_material(mesh, material)

    mesh["remi_texture_albedo"] = str(albedo_path)
    mesh["remi_texture_albedo_image"] = albedo.name
    mesh["remi_texture_normal"] = str(normal_path)
    mesh["remi_texture_normal_image"] = normal_image.name
    mesh["remi_texture_normal_uv"] = normal_uv
    mesh["remi_texture_confidence"] = str(confidence_path)
    mesh["remi_texture_report"] = str(paths.get("report", ""))
    mesh["remi_texture_uv"] = uv_name
    mesh["remi_texture_source"] = str(source_label_value)
    mesh["remi_texture_normal_mode"] = str(normal_mode)
    mesh["remi_texture_material"] = material.name
    mesh["remi_texture_confidence_material"] = confidence_material.name
    mesh["remi_texture_preview"] = "TEXTURE"
    return material
