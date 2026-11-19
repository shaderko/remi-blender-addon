"""Native Blender 5.3 Gaussian point clouds.

Remi requires Blender's built-in GAUSSIAN_SPLAT representation. It preserves the
complete Gaussian data, not a reconstructed surface.
"""
from pathlib import Path

import bpy
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
    """Read the complete Gaussian data stored on a native splat object.

    No source archive is required, so moving or deleting the generation folder
    cannot break meshing for an already imported model.
    """
    if not is_splat_object(obj):
        raise ValueError("Select a Remi Gaussian splat object first")
    if not uses_native_splats(obj):
        raise ValueError("Remi Gaussian object has no native Gaussian data")
    attributes = obj.data.attributes
    radiance = _read_attribute(attributes, "radiance:base", 4)
    return {
        "positions": _read_attribute(attributes, "position", 3),
        "scales": _read_attribute(attributes, "scale", 3),
        "rotations": _read_attribute(attributes, "rotation", 4, "value"),
        "opacities": radiance[:, 3].copy(),
        "sh_dc": radiance[:, :3].copy(),
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


def _attribute(geometry, name, kind, values, property_name="vector"):
    attribute = geometry.attributes.get(name)
    if attribute is None:
        attribute = geometry.attributes.new(name, kind, "POINT")
    if attribute.data_type != kind or attribute.domain != "POINT":
        raise ValueError(f"Incompatible Gaussian attribute: {name}")
    attribute.data.foreach_set(property_name, np.asarray(values, dtype=np.float32).ravel())


def _tag(obj, path, count):
    obj[SPLAT_TAG] = 1
    obj["remi_splat_schema"] = 1
    obj["remi_splat_archive"] = str(path)
    obj["remi_splat_ply"] = str(path.with_name("model.ply"))
    obj["remi_splat_count"] = count
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
        _tag(obj, path, count)
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


def import_splats(context, path):
    """Import a splats.npz archive as a native Blender 5.3 Gaussian cloud."""
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before importing splats")
    if not native_splats_available():
        raise RuntimeError("Remi requires Blender 5.3 with native GAUSSIAN_SPLAT support")
    path = Path(path).resolve()
    data = load(path)  # Validate everything before creating any Blender data.
    return _import_native(context, path, data)
