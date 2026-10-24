"""Projection layer stack and layered base-colour material construction."""
from __future__ import annotations

from pathlib import Path

import bpy

from .splat_projection import existing_normal_map


def _unique_name(mesh, base):
    existing = {layer.name for layer in mesh.remi_texture_layers}
    if base not in existing:
        return base
    index = 2
    while f"{base} {index}" in existing:
        index += 1
    return f"{base} {index}"


def _load_image(name, path, *, pack=True):
    path = str(path)
    if not Path(path).is_file():
        raise ValueError(f"Projection layer image not found: {path}")
    image = bpy.data.images.get(name)
    if image is None:
        image = bpy.data.images.load(path, check_existing=False)
        image.name = name
    else:
        image.filepath = path
        image.reload()
    image.colorspace_settings.name = "sRGB"
    image.file_format = "PNG"
    image.use_fake_user = True
    if pack:
        image.pack()
    return image


def _image_for_layer(layer):
    if layer.image_name:
        image = bpy.data.images.get(layer.image_name)
        if image is not None:
            return image
    if not layer.image_path:
        return None
    try:
        image = _load_image(
            layer.image_name or f"Remi Layer · {Path(layer.image_path).stem}",
            layer.image_path,
            pack=False,
        )
        layer.image_name = image.name
        return image
    except ValueError:
        return None


def add_layer(
    mesh,
    name,
    image_path,
    *,
    kind="VIEW",
    alpha_mode="IMAGE",
    source="",
    visible=True,
    opacity=1.0,
    pack=True,
    rebuild=True,
):
    layer = mesh.remi_texture_layers.add()
    layer.name = _unique_name(mesh, name)
    try:
        image = _load_image(f"{mesh.name} · {layer.name}", image_path, pack=pack)
    except Exception:
        mesh.remi_texture_layers.remove(len(mesh.remi_texture_layers) - 1)
        raise
    layer.kind = kind
    layer.alpha_mode = alpha_mode
    layer.image_path = str(image_path)
    layer.image_name = image.name
    layer.source = source
    layer.visible = visible
    layer.opacity = opacity
    if rebuild:
        rebuild_layer_material(mesh)
    return layer


def ordered_layers(mesh):
    """Return layers in material order: base first, then view layers."""
    base = [layer for layer in mesh.remi_texture_layers if layer.kind == "BASE"]
    views = [layer for layer in mesh.remi_texture_layers if layer.kind != "BASE"]
    return base + views


def ordered_layer_indices(mesh):
    """Return ``(collection_index, layer)`` in material order."""
    values = list(enumerate(mesh.remi_texture_layers))
    values.sort(key=lambda item: 0 if item[1].kind == "BASE" else 1)
    return values


def sync_base_layer(mesh):
    """Add or update the 3D projection base layer from the current mesh images."""
    path = mesh.get("remi_texture_albedo")
    image_name = mesh.get("remi_texture_albedo_image")
    if not path and not image_name:
        return None
    image = bpy.data.images.get(str(image_name)) if image_name else None
    if image is None and path and Path(str(path)).is_file():
        image = _load_image(image_name or f"{mesh.name} · projected albedo", path, pack=False)
    if image is None:
        return None
    layer = next((item for item in mesh.remi_texture_layers if item.kind == "BASE"), None)
    if layer is None:
        layer = mesh.remi_texture_layers.add()
        layer.name = _unique_name(mesh, "3D Base")
    layer.kind = "BASE"
    layer.alpha_mode = "OPAQUE"
    layer.image_path = str(path or image.filepath)
    layer.image_name = image.name
    layer.source = "3D splat projection"
    layer.visible = True
    layer.opacity = 1.0
    return layer


def delete_layer(mesh, index):
    if not 0 <= index < len(mesh.remi_texture_layers):
        return False
    mesh.remi_texture_layers.remove(index)
    rebuild_layer_material(mesh)
    return True


def rebuild_layer_material(mesh):
    """Build a base-colour chain from the visible projection layers."""
    if mesh is None or mesh.type != "MESH":
        return None
    uv_name = str(mesh.get("remi_texture_uv", ""))
    if not uv_name and mesh.data.uv_layers:
        uv_name = mesh.data.uv_layers.active.name

    normal_image, normal_uv = existing_normal_map(mesh)
    if normal_image is None:
        normal_image = bpy.data.images.get(str(mesh.get("remi_texture_normal_image", "")))
    if normal_image is None:
        normal_path = mesh.get("remi_texture_normal")
        if normal_path and Path(str(normal_path)).is_file():
            normal_image = _load_image(
                f"{mesh.name} · projected normal", normal_path, pack=False
            )
    if normal_image is not None:
        normal_image.colorspace_settings.name = "Non-Color"
    normal_uv = normal_uv or str(mesh.get("remi_texture_normal_uv", "")) or uv_name

    material = bpy.data.materials.get(f"{mesh.name} · layered")
    if material is None:
        material = bpy.data.materials.new(name=f"{mesh.name} · layered")
    material.use_nodes = True
    material.blend_method = "OPAQUE"
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (720, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (420, 0)
    bsdf.inputs["Roughness"].default_value = 0.7
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.0
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    previous = nodes.new("ShaderNodeRGB")
    previous.name = "remi_layer_base"
    previous.label = "Neutral base"
    previous.location = (-420, 180)
    previous.outputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)

    visible_index = 0
    for index, layer in enumerate(ordered_layers(mesh)):
        if not layer.visible:
            continue
        image = _image_for_layer(layer)
        if image is None:
            continue
        texture = nodes.new("ShaderNodeTexImage")
        texture.name = f"remi_layer_{index}"
        texture.label = layer.name
        texture.image = image
        texture.interpolation = "Linear"
        texture.extension = "EXTEND"
        texture.location = (-420, -160 * visible_index)
        if uv_name and hasattr(texture, "uv_map"):
            texture.uv_map = uv_name

        mix = nodes.new("ShaderNodeMixRGB")
        mix.name = f"remi_layer_mix_{index}"
        mix.blend_type = "MIX"
        mix.location = (120 - 80 * min(visible_index, 3), 180 - 160 * visible_index)
        links.new(previous.outputs["Color"], mix.inputs["Color1"])
        links.new(texture.outputs["Color"], mix.inputs["Color2"])
        if layer.alpha_mode == "IMAGE":
            multiply = nodes.new("ShaderNodeMath")
            multiply.operation = "MULTIPLY"
            multiply.name = f"remi_layer_alpha_{index}"
            multiply.location = (-100, -160 * visible_index)
            links.new(texture.outputs["Alpha"], multiply.inputs[0])
            multiply.inputs[1].default_value = float(layer.opacity)
            links.new(multiply.outputs["Value"], mix.inputs["Fac"])
        else:
            mix.inputs["Fac"].default_value = float(layer.opacity)
        previous = mix
        visible_index += 1

    links.new(previous.outputs["Color"], bsdf.inputs["Base Color"])

    if normal_image is not None:
        normal_texture = nodes.new("ShaderNodeTexImage")
        normal_texture.name = "remi_normal_texture"
        normal_texture.label = "Projected normal"
        normal_texture.image = normal_image
        normal_texture.interpolation = "Linear"
        normal_texture.extension = "EXTEND"
        normal_texture.location = (-420, -520)
        if normal_uv and hasattr(normal_texture, "uv_map"):
            normal_texture.uv_map = normal_uv
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.location = (-80, -520)
        if normal_uv and hasattr(normal_map, "uv_map"):
            normal_map.uv_map = normal_uv
        links.new(normal_texture.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

    mesh.data.materials.clear()
    mesh.data.materials.append(material)
    mesh["remi_texture_material"] = material.name
    mesh["remi_texture_preview"] = "TEXTURE"
    mesh["remi_texture_layered"] = 1
    material.diffuse_color = (0.55, 0.58, 0.62, 1.0)
    return material
