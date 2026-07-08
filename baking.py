"""
Texture baking for Remi.
Bakes diffuse, roughness, and normal maps from the original
high-poly mesh onto the remeshed/decimated result.
"""

import bpy
import mathutils


def _ensure_uv(obj: bpy.types.Object, method: str = "SMART", island_margin: float = 0.02):
    """Create a UV map on the target mesh if it doesn't have one."""
    if obj.data.uv_layers:
        return
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    if method == "LIGHTMAP":
        bpy.ops.uv.lightmap_pack(PREF_BOX_DIV=12, PREF_MARGIN_DIV=island_margin)
    else:
        bpy.ops.uv.smart_project(angle_limit=66, island_margin=island_margin)

    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"Baking: Created UV map on '{obj.name}' ({method})")


def _make_world_space_copy(obj: bpy.types.Object, name: str) -> bpy.types.Object:
    """Create a duplicate with all modifiers + transform applied (world-space)."""
    dup = obj.copy()
    dup.data = obj.data.copy()
    bpy.context.collection.objects.link(dup)
    bpy.context.view_layer.objects.active = dup
    dup.select_set(True)
    # Apply modifiers (iterate in reverse since applying removes them)
    for mod in list(dup.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception:
            pass
    # Bake transform into vertices
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    dup.name = name
    return dup


def _create_bake_images(name_prefix: str, size: int) -> dict:
    """Create blank image textures for baking."""
    images = {}
    # Color space per channel: diffuse=sRGB for display, rough/normal=Non-Color
    for key, suffix, color, cs in [
        ("diffuse", "_diffuse", (0.5, 0.5, 0.5, 1.0), "sRGB"),
        ("roughness", "_roughness", (0.5, 0.5, 0.5, 1.0), "Non-Color"),
        ("normal", "_normal", (0.5, 0.5, 1.0, 1.0), "Non-Color"),
    ]:
        img = bpy.data.images.new(name=f"{name_prefix}{suffix}", width=size, height=size, alpha=True)
        img.generated_color = color
        img.colorspace_settings.name = cs
        img.file_format = "PNG"
        images[key] = img
    return images


def _build_bake_material(obj: bpy.types.Object, images: dict) -> dict:
    """Create a material on the object with image-texture nodes for each bake channel.

    Returns dict of {channel_name: ShaderNodeTexImage} for each image node.
    """
    mat = bpy.data.materials.new(name=f"{obj.name}_baked")
    mat.use_nodes = True
    mat.blend_method = "OPAQUE"
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Principled BSDF
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (400, 0)

    # Output
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (700, 0)
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    # Position helper
    def tex_node(name, img, x, y):
        n = nodes.new("ShaderNodeTexImage")
        n.location = (x, y)
        n.image = img
        n.name = name
        n.label = name
        n.select = False
        return n

    channels = {}

    # Diffuse → Base Color
    n = tex_node("bake_diffuse", images["diffuse"], -200, 400)
    links.new(n.outputs["Color"], bsdf.inputs["Base Color"])
    channels["diffuse"] = n

    # Roughness
    n = tex_node("bake_roughness", images["roughness"], -200, 150)
    links.new(n.outputs["Color"], bsdf.inputs["Roughness"])
    channels["roughness"] = n

    # Normal
    tex_n = tex_node("bake_normal", images["normal"], -200, -100)
    nmap = nodes.new("ShaderNodeNormalMap")
    nmap.location = (50, -100)
    links.new(tex_n.outputs["Color"], nmap.inputs["Color"])
    links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    channels["normal"] = tex_n

    # Assign material
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    return channels


def bake_textures(
    source_original: bpy.types.Object,
    target_result: bpy.types.Object,
    texture_size: int = 2048,
    final_name: str = "",
    uv_method: str = "SMART",
    uv_island_margin: float = 0.02,
) -> dict:
    """Bake diffuse, roughness, and normal maps from source to target.

    Both objects must overlap in world space. This function creates a
    world-space copy of the source for baking, then cleans it up.

    Returns dict with keys 'success' and 'images' (list of created image names).
    """
    scene = bpy.context.scene
    prev_engine = scene.render.engine

    # Use final_name for image naming if provided
    img_base = final_name or target_result.name

    # 1. Ensure the target has UVs
    _ensure_uv(target_result, method=uv_method, island_margin=uv_island_margin)

    # 2. Create a world-space copy of the original for baking (source)
    temp_source = _make_world_space_copy(source_original, "_bake_source_tmp")

    # 3. Create blank images (use final_name for clean naming)
    images = _create_bake_images(img_base, texture_size)

    # 4. Build material on target with image nodes
    channels = _build_bake_material(target_result, images)
    bake_mat = target_result.data.materials[0]

    # 5. Set up scene for baking
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 128

    # Select source and make target active
    bpy.ops.object.select_all(action="DESELECT")
    temp_source.select_set(True)
    target_result.select_set(True)
    bpy.context.view_layer.objects.active = target_result

    # Configure bake settings (Blender 5.1+)
    bake_st = scene.render.bake
    bake_st.use_selected_to_active = True
    bake_st.margin = 16
    bake_st.use_pass_direct = False
    bake_st.use_pass_indirect = False
    bake_st.use_pass_color = True
    bake_st.target = "IMAGE_TEXTURES"
    bake_st.use_clear = True
    # Small cage extrusion helps rays reach the source when the remesh
    # surface doesn't perfectly align with the original (common at large scales).
    bake_st.cage_extrusion = 0.01

    # In Blender 5.1, the bake TYPE is passed directly to the operator,
    # not set on BakeSettings (which only accepts NORMALS/DISPLACEMENT).
    # Blender 5.1 valid bake types:
    # COMBINED, AO, SHADOW, POSITION, NORMAL, UV, ROUGHNESS, EMIT,
    # ENVIRONMENT, DIFFUSE, GLOSSY, TRANSMISSION
    bake_configs = [
        ("diffuse", "GLOSSY"),
        ("roughness", "ROUGHNESS"),
        ("normal", "NORMAL"),
    ]

    for channel, bake_type in bake_configs:
        node = channels[channel]
        bake_mat.node_tree.nodes.active = node
        node.select = True

        bpy.ops.object.bake(type=bake_type)

    # 6. Cleanup
    bpy.ops.object.select_all(action="DESELECT")
    bpy.data.objects.remove(temp_source, do_unlink=True)
    scene.render.engine = prev_engine

    image_names = list(images.keys())
    print(f"Baking: Done — created {image_names}")

    return {
        "success": True,
        "images": image_names,
    }
