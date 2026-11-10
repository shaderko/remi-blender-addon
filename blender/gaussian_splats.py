"""Native Gaussian point clouds on Blender 5.3+, with an older-version preview.

Both paths preserve the complete Gaussian data, not a reconstructed surface.
Native rendering uses Blender's built-in GAUSSIAN_SPLAT representation; the
fallback uses explicitly labeled, non-destructive ellipsoid proxies.
"""
from pathlib import Path

import bpy
from mathutils import Quaternion
import numpy as np

from ..integrations.triposplat.splat_data import load, save


SPLAT_TAG = "remi_gaussian_splats"


def is_splat_object(obj):
    return bool(obj and obj.get(SPLAT_TAG))


def splat_for_image(image_path):
    """Return the imported splat generated from this source image, if any."""
    try:
        target = str(Path(image_path).resolve())
    except (OSError, TypeError, ValueError):
        return None
    for obj in bpy.data.objects:
        if is_splat_object(obj) and obj.get("remi_source_image") == target:
            return obj
    return None


def _read_attribute(attributes, name, width, property_name="vector"):
    attribute = attributes.get(name)
    if attribute is None:
        raise ValueError(f"Gaussian object is missing the {name!r} attribute")
    values = np.empty(len(attribute.data) * width, dtype=np.float32)
    attribute.data.foreach_get(property_name, values)
    return values.reshape(-1, width)


def splat_arrays(obj):
    """Read the complete Gaussian data stored on a scene object.

    Native point clouds and the ellipsoid-preview mesh both carry the original
    values. No source archive is required, so moving or deleting the generation
    folder cannot break meshing for an already imported model.
    """
    if not is_splat_object(obj):
        raise ValueError("Select a Remi Gaussian splat object first")
    if uses_native_splats(obj):
        attributes = obj.data.attributes
        radiance = _read_attribute(attributes, "radiance:base", 4)
        return {
            "positions": _read_attribute(attributes, "position", 3),
            "scales": _read_attribute(attributes, "scale", 3),
            "rotations": _read_attribute(attributes, "rotation", 4, "value"),
            "opacities": radiance[:, 3].copy(),
            "sh_dc": radiance[:, :3].copy(),
        }
    if obj.type != "MESH":
        raise ValueError("Remi Gaussian object has no readable Gaussian data")
    mesh = obj.data
    count = len(mesh.vertices)
    positions = np.empty(count * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", positions)
    rotations = np.column_stack([
        _read_attribute(mesh.attributes, f"remi_splat_rot_{component}", 1, "value")[:, 0]
        for component in "wxyz"
    ])
    return {
        "positions": positions.reshape(-1, 3),
        "scales": _read_attribute(mesh.attributes, "remi_splat_scale", 3),
        "rotations": rotations,
        "opacities": _read_attribute(mesh.attributes, "remi_splat_opacity", 1, "value")[:, 0],
        "sh_dc": _read_attribute(mesh.attributes, "remi_splat_sh_dc", 3),
    }


def write_splat_archive(obj, path):
    """Write an ephemeral exchange archive from the object's current data."""
    save(path, splat_arrays(obj))
    return Path(path)


def covariance_normals(arrays, view_matrix=None, object_matrix=None):
    """Estimate one normal per Gaussian from its flattest covariance axis.

    Standard 3DGS has orientation and anisotropic scale but no stored normal.
    The shortest ellipsoid axis is the usual surfel normal estimate. When a
    view matrix is supplied, normals are oriented toward the camera so the pass
    represents the visible side of the splat surface.
    """
    positions = np.asarray(arrays["positions"], dtype=np.float32)
    scales = np.asarray(arrays["scales"], dtype=np.float32)
    rotations = np.asarray(arrays["rotations"], dtype=np.float32)
    q = rotations / np.maximum(np.linalg.norm(rotations, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q.T
    rotation = np.stack(
        (
            1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
            2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
            2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
        ),
        axis=1,
    ).reshape(-1, 3, 3)
    covariance = np.einsum(
        "nij,nj,nkj->nik", rotation, np.maximum(scales, 1e-8) ** 2, rotation
    )
    _values, eigenvectors = np.linalg.eigh(covariance)
    normals = eigenvectors[:, :, 0].astype(np.float32)
    if object_matrix is not None:
        matrix = np.asarray(object_matrix, dtype=np.float64).reshape(4, 4)
        linear = matrix[:3, :3]
        positions = (
            positions @ linear.T + matrix[:3, 3]
        ).astype(np.float32)
        normal_matrix = np.linalg.inv(linear).T
        normals = (normals @ normal_matrix.T).astype(np.float32)
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-8)
    if view_matrix is not None:
        view = np.asarray(view_matrix, dtype=np.float64).reshape(4, 4)
        camera = np.linalg.inv(view)[:3, 3]
        toward_camera = camera[None, :] - positions
        normals[np.sum(normals * toward_camera, axis=1) < 0.0] *= -1.0
    return normals


def create_normal_splat(context, splat, view_matrix):
    """Create a temporary native splat object whose radiance encodes normals.

    Blender's native Gaussian renderer composites radiance:base with the real
    splat alpha. Reusing it for encoded covariance normals gives a normal pass
    with exactly the same visibility, overlap and silhouette as the colour
    render. The returned object must be removed with ``remove_normal_splat``.
    """
    if not uses_native_splats(splat):
        return None
    arrays = splat_arrays(splat)
    normals = covariance_normals(
        arrays, view_matrix, object_matrix=splat.matrix_world
    )
    encoded = np.clip(normals * 0.5 + 0.5, 0.0, 1.0)
    sh_dc = (encoded - 0.5) / 0.28209479177387814
    radiance = np.column_stack((sh_dc, arrays["opacities"])).astype(np.float32)
    data = splat.data.copy()
    attribute = data.attributes.get("radiance:base")
    if attribute is None:
        bpy.data.pointclouds.remove(data)
        raise RuntimeError("Native Gaussian data has no radiance attribute")
    attribute.data.foreach_set("vector", radiance.ravel())
    obj = bpy.data.objects.new(f"{splat.name} \u00b7 normal pass", data)
    obj.matrix_world = splat.matrix_world.copy()
    context.scene.collection.objects.link(obj)
    obj.hide_set(False)
    return obj


def remove_normal_splat(obj):
    if obj is None:
        return
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        bpy.data.pointclouds.remove(data)


def native_splats_available():
    property_type = bpy.types.PointCloud.bl_rna.properties.get("type")
    return bool(property_type and "GAUSSIAN_SPLAT" in property_type.enum_items)


def uses_native_splats(obj):
    return bool(obj and obj.type == "POINTCLOUD" and getattr(obj.data, "type", None) == "GAUSSIAN_SPLAT")


def splat_display_label(obj):
    return "native Gaussian rendering" if uses_native_splats(obj) else "ellipsoid preview"


def _attribute(geometry, name, kind, values, property_name="vector"):
    attribute = geometry.attributes.get(name)
    if attribute is None:
        attribute = geometry.attributes.new(name, kind, "POINT")
    if attribute.data_type != kind or attribute.domain != "POINT":
        raise ValueError(f"Incompatible Gaussian attribute: {name}")
    attribute.data.foreach_set(property_name, np.asarray(values, dtype=np.float32).ravel())


def _tag(obj, path, count, display, preview_count):
    obj[SPLAT_TAG] = 1
    obj["remi_splat_schema"] = 1
    obj["remi_splat_archive"] = str(path)
    obj["remi_splat_ply"] = str(path.with_name("model.ply"))
    obj["remi_splat_count"] = count
    obj["remi_splat_preview_count"] = preview_count
    obj["remi_splat_display"] = display
    obj["remi_splat_frame"] = "Z-up; rotations WXYZ; activated scales/opacity; SH DC"


def create_native_splat(path, data, collection):
    """Match Blender 5.3's own PLY/glTF importers, without re-encoding data.

    radiance:base is FLOAT4 (raw SH DC, linear opacity), NOT a color attribute.
    Blender evaluates the SH basis, sRGB conversion and Gaussian falloff itself.
    Scale is activated XYZ sigma, rotation is Blender's WXYZ quaternion.
    No material or Geometry Nodes modifier is needed for default native shading.
    """
    count = len(data["positions"])
    cloud = bpy.data.pointclouds.new(f"{path.parent.name} · Native Gaussian data")
    obj = None
    try:
        cloud.resize(count)
        cloud.type = "GAUSSIAN_SPLAT"
        _attribute(cloud, "position", "FLOAT_VECTOR", data["positions"])
        _attribute(cloud, "scale", "FLOAT_VECTOR", data["scales"])
        _attribute(cloud, "rotation", "QUATERNION", data["rotations"], "value")
        radiance = np.column_stack((data["sh_dc"], data["opacities"]))
        _attribute(cloud, "radiance:base", "FLOAT4", radiance)
        obj = bpy.data.objects.new(f"Remi Splats · {path.parent.name}", cloud)
        _tag(obj, path, count, "NATIVE", count)
        collection.objects.link(obj)
        return obj
    except Exception:
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.pointclouds.remove(cloud)
        raise


def _import_native(context, path, data):
    obj = create_native_splat(path, data, context.collection)
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def _named(nodes, name, kind="FLOAT_VECTOR"):
    node = nodes.new("GeometryNodeInputNamedAttribute")
    node.data_type = kind
    node.inputs["Name"].default_value = name
    return node.outputs["Attribute"]


def _material():
    material = bpy.data.materials.new("Remi · Gaussian color preview")
    try:
        material.use_nodes = True
        material.diffuse_color = (0.35, 0.6, 0.8, 1)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.clear()
        color = nodes.new("ShaderNodeAttribute")
        color.attribute_name = "remi_splat_color"
        emission = nodes.new("ShaderNodeEmission")
        output = nodes.new("ShaderNodeOutputMaterial")
        links.new(color.outputs["Color"], emission.inputs["Color"])
        links.new(emission.outputs[0], output.inputs["Surface"])
        return material
    except Exception:
        bpy.data.materials.remove(material)
        raise


def _preview(obj, material):
    group = bpy.data.node_groups.new("Remi · Ellipsoid preview (not a mesh conversion)", "GeometryNodeTree")
    modifier = obj.modifiers.new("Gaussian ellipsoid preview", "NODES")
    modifier.node_group = group
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    source = nodes.new("NodeGroupInput")
    output = nodes.new("NodeGroupOutput")
    separate = nodes.new("GeometryNodeSeparateGeometry")
    separate.domain = "POINT"
    links.new(source.outputs["Geometry"], separate.inputs["Geometry"])
    links.new(_named(nodes, "remi_splat_preview", "BOOLEAN"), separate.inputs["Selection"])
    points = nodes.new("GeometryNodeMeshToPoints")
    points.mode = "VERTICES"
    links.new(separate.outputs["Selection"], points.inputs["Mesh"])
    sphere = nodes.new("GeometryNodeMeshIcoSphere")
    sphere.inputs["Radius"].default_value = 1.0
    sphere.inputs["Subdivisions"].default_value = 1
    instances = nodes.new("GeometryNodeInstanceOnPoints")
    links.new(points.outputs["Points"], instances.inputs["Points"])
    links.new(sphere.outputs["Mesh"], instances.inputs["Instance"])
    links.new(_named(nodes, "remi_splat_scale"), instances.inputs["Scale"])
    links.new(_named(nodes, "remi_splat_euler"), instances.inputs["Rotation"])
    realize = nodes.new("GeometryNodeRealizeInstances")
    links.new(instances.outputs["Instances"], realize.inputs["Geometry"])
    material_node = nodes.new("GeometryNodeSetMaterial")
    material_node.inputs["Material"].default_value = material
    links.new(realize.outputs["Geometry"], material_node.inputs["Geometry"])
    links.new(material_node.outputs["Geometry"], output.inputs["Geometry"])
    return group


def _import_ellipsoids(context, path, data, preview_limit):
    count = len(data["positions"])
    mesh = bpy.data.meshes.new(f"{path.parent.name} · Gaussian data")
    obj = material = group = None
    selected = list(context.selected_objects)
    active = context.view_layer.objects.active
    try:
        mesh.vertices.add(count)
        mesh.vertices.foreach_set("co", data["positions"].ravel())
        _attribute(mesh, "remi_splat_scale", "FLOAT_VECTOR", data["scales"])
        _attribute(mesh, "remi_splat_sh_dc", "FLOAT_VECTOR", data["sh_dc"])
        _attribute(mesh, "remi_splat_opacity", "FLOAT", data["opacities"], "value")
        for index, component in enumerate("wxyz"):
            _attribute(mesh, f"remi_splat_rot_{component}", "FLOAT", data["rotations"][:, index], "value")
        euler = np.array([Quaternion(row).to_euler("XYZ") for row in data["rotations"]], dtype=np.float32)
        _attribute(mesh, "remi_splat_euler", "FLOAT_VECTOR", euler)
        color = np.ones((count, 4), dtype=np.float32)
        color[:, :3] = np.clip(data["sh_dc"] * 0.28209479177387814 + 0.5, 0, 1)
        # Display colors are sRGB; Blender's shader attributes are scene-linear.
        rgb = color[:, :3]
        color[:, :3] = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        color[:, 3] = data["opacities"]
        _attribute(mesh, "remi_splat_color", "FLOAT_COLOR", color, "color")
        visible = np.flatnonzero(data["opacities"] >= 0.05)
        limit = max(1, int(preview_limit))
        if len(visible) > limit:
            visible = visible[np.linspace(0, len(visible) - 1, limit, dtype=np.int64)]
        mask = np.zeros(count, dtype=np.bool_)
        mask[visible] = True
        attribute = mesh.attributes.new("remi_splat_preview", "BOOLEAN", "POINT")
        attribute.data.foreach_set("value", mask)
        mesh.update()
        obj = bpy.data.objects.new(f"Remi Splats · {path.parent.name}", mesh)
        _tag(obj, path, count, "ELLIPSOID", len(visible))
        material = _material()
        mesh.materials.append(material)
        group = _preview(obj, material)
        context.collection.objects.link(obj)
        for other in selected:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return obj
    except Exception:
        # A failed import must not leave partial objects or orphan resources.
        if obj is not None:
            for modifier in obj.modifiers:
                if modifier.type == "NODES" and modifier.node_group is not None:
                    group = modifier.node_group
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
        if material is not None and material.users == 0:
            bpy.data.materials.remove(material)
        for other in selected:
            other.select_set(True)
        context.view_layer.objects.active = active
        raise


def import_splats(context, path, *, preview_limit=32768, display="AUTO"):
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before importing splats")
    if display not in {"AUTO", "NATIVE", "ELLIPSOID"}:
        raise ValueError(f"Unknown Gaussian display: {display}")
    native = native_splats_available()
    if display == "NATIVE" and not native:
        raise ValueError("Native Gaussian rendering requires Blender 5.3+ with GAUSSIAN_SPLAT support")
    path = Path(path).resolve()
    data = load(path)  # Validate everything before creating any Blender data.
    if native and display != "ELLIPSOID":
        return _import_native(context, path, data)
    return _import_ellipsoids(context, path, data, preview_limit)
