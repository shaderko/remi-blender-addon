"""
Texture baking for Remi.
Bakes albedo, roughness, normal, and ambient-occlusion maps from the original
high-poly mesh onto the remeshed/decimated result.
"""

import math

import bpy

from .uv_mapping import ensure_remi_uv


def _ensure_uv(
    obj: bpy.types.Object,
    method: str = "REMI",
    island_margin: float = 0.02,
    auto_unwrap: bool = True,
    profile: str = "NORMAL_BAKE",
    texture_size: int = 2048,
    margin_px: int = 4,
    preserve_existing_seams: bool = True,
):
    """Ensure the target has UVs, optionally generating them automatically."""
    if obj.data.uv_layers and (method != "REMI" or not auto_unwrap):
        return True
    if not auto_unwrap:
        return False

    if method == "REMI":
        result = ensure_remi_uv(
            obj,
            profile_id=profile,
            texture_size=texture_size,
            margin_px=margin_px,
            preserve_existing_seams=preserve_existing_seams,
        )
        if not result.success:
            print(f"Baking: Remi UV failed on '{obj.name}': {result.error}")
        return result.success

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    if method == "LIGHTMAP":
        bpy.ops.uv.lightmap_pack(PREF_BOX_DIV=12, PREF_MARGIN_DIV=island_margin)
    else:
        bpy.ops.uv.smart_project(
            angle_limit=math.radians(66.0),
            margin_method="FRACTION",
            island_margin=island_margin,
        )

    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"Baking: Created UV map on '{obj.name}' ({method})")
    return True


def _scale_obj(obj: bpy.types.Object, factor: float):
    """Uniform-scale an object's vertex data directly."""
    import bmesh
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    for v in bm.verts:
        v.co *= factor
    bm.to_mesh(me)
    bm.free()
    me.update()
    # Force depsgraph to pick up the mesh change
    bpy.context.view_layer.update()


def _make_world_space_copy(obj: bpy.types.Object, name: str) -> bpy.types.Object:
    """Create a duplicate with all modifiers + transform applied (world-space)."""
    dup = obj.copy()
    dup.data = obj.data.copy()
    # The baking source may need temporary material edits.  Give it private
    # materials so the original object's shader setup is never changed.
    dup.data.materials.clear()
    for material in obj.data.materials:
        dup.data.materials.append(material.copy() if material else None)
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


def _create_bake_images(name_prefix: str, size: int, channels: tuple[str, ...]) -> dict:
    """Create or reuse blank image textures for the requested bake channels."""
    images = {}
    # Albedo is color-managed for display; data maps are Non-Color.
    for key, suffix, color, cs in [
        ("diffuse", "_diffuse", (0.5, 0.5, 0.5, 1.0), "sRGB"),
        ("roughness", "_roughness", (0.5, 0.5, 0.5, 1.0), "Non-Color"),
        ("normal", "_normal", (0.5, 0.5, 1.0, 1.0), "Non-Color"),
        ("ao", "_ao", (1.0, 1.0, 1.0, 1.0), "Non-Color"),
    ]:
        if key not in channels:
            continue
        image_name = f"{name_prefix}{suffix}"
        img = bpy.data.images.get(image_name)
        if img is None:
            img = bpy.data.images.new(name=image_name, width=size, height=size, alpha=True)
        elif img.size[0] != size or img.size[1] != size:
            img.scale(size, size)
        img.generated_color = color
        img.colorspace_settings.name = cs
        img.file_format = "PNG"
        images[key] = img
    return images


def _build_bake_material(obj: bpy.types.Object, images: dict) -> dict:
    """Create or update Remi's baked material and return image nodes by channel."""
    material_name = f"{obj.name}_baked"
    mat = bpy.data.materials.get(material_name)
    if mat is None:
        mat = bpy.data.materials.new(name=material_name)
    mat.use_nodes = True
    mat.blend_method = "OPAQUE"
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (400, 0)
    out = next((node for node in nodes if node.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        out = nodes.new("ShaderNodeOutputMaterial")
        out.location = (700, 0)
    if not bsdf.outputs["BSDF"].is_linked:
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

    def existing_or_new(name, image, x, y):
        node = nodes.get(name)
        if node is None or node.type != "TEX_IMAGE":
            node = tex_node(name, image, x, y)
        node.image = image
        return node

    if "diffuse" in images:
        n = existing_or_new("bake_diffuse", images["diffuse"], -200, 400)
        links.new(n.outputs["Color"], bsdf.inputs["Base Color"])
        channels["diffuse"] = n
    if "roughness" in images:
        n = existing_or_new("bake_roughness", images["roughness"], -200, 150)
        links.new(n.outputs["Color"], bsdf.inputs["Roughness"])
        channels["roughness"] = n
    if "normal" in images:
        tex_n = existing_or_new("bake_normal", images["normal"], -200, -100)
        nmap = nodes.get("bake_normal_map")
        if nmap is None or nmap.type != "NORMAL_MAP":
            nmap = nodes.new("ShaderNodeNormalMap")
            nmap.name = "bake_normal_map"
            nmap.location = (50, -100)
        links.new(tex_n.outputs["Color"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        channels["normal"] = tex_n
    if "ao" in images:
        # AO is a separate data map, intentionally not wired into the shader.
        channels["ao"] = existing_or_new("bake_ao", images["ao"], -200, -350)

    # Every target face must use a material with the active bake image.  This
    # is especially important for remeshes that retain multiple material slots.
    if obj.data.materials:
        for index in range(len(obj.data.materials)):
            obj.data.materials[index] = mat
    else:
        obj.data.materials.append(mat)

    return channels


def _prepare_albedo_emission(obj: bpy.types.Object):
    """Turn private source materials into base-color emission materials.

    A DIFFUSE closure bake is affected by Metallic and other BSDF behavior.
    For a true albedo map, Remi bakes the Principled Base Color through
    emission on disposable source copies. Texture-node links are preserved.
    """
    for slot in obj.data.materials:
        if not slot or not slot.node_tree:
            continue
        nodes = slot.node_tree.nodes
        links = slot.node_tree.links
        bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
        output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL"), None)
        if bsdf is None or output is None:
            continue
        base_color = bsdf.inputs.get("Base Color")
        if base_color is None:
            continue
        emission = nodes.get("_remi_albedo_emission")
        if emission is None or emission.type != "EMISSION":
            emission = nodes.new("ShaderNodeEmission")
            emission.name = "_remi_albedo_emission"
            emission.label = "Remi Albedo Bake"
            emission.location = (bsdf.location.x + 250, bsdf.location.y)
        emission.inputs["Strength"].default_value = 1.0
        if base_color.is_linked:
            links.new(base_color.links[0].from_socket, emission.inputs["Color"])
        else:
            emission.inputs["Color"].default_value = base_color.default_value
        for link in list(output.inputs["Surface"].links):
            links.remove(link)
        links.new(emission.outputs["Emission"], output.inputs["Surface"])


def bake_textures(
    source_original: bpy.types.Object | list[bpy.types.Object],
    target_result: bpy.types.Object,
    texture_size: int = 2048,
    final_name: str = "",
    uv_method: str = "REMI",
    uv_island_margin: float = 0.02,
    uv_profile: str = "NORMAL_BAKE",
    uv_margin_px: int = 4,
    uv_preserve_seams: bool = True,
    auto_unwrap: bool = True,
    recalc_normals: bool = True,
    cage_extrusion: float = 0.1,
    max_ray_distance: float = 0.0,
    passes: tuple[str, ...] = ("diffuse", "roughness", "normal", "ao"),
) -> dict:
    """Bake albedo, roughness, normal, and AO maps from source to target.

    Source and target meshes must overlap in world space. This function
    accepts one source or a list of source meshes, creates world-space copies
    for baking, then cleans them up.

    Returns dict with keys 'success' and 'images' (list of created image names).
    """
    scene = bpy.context.scene
    prev_engine = scene.render.engine

    # Use final_name for image naming if provided
    img_base = final_name or target_result.name

    valid_passes = ("diffuse", "roughness", "normal", "ao")
    passes = tuple(channel for channel in passes if channel in valid_passes)
    if not passes:
        return {"success": False, "images": [], "error": "No valid bake passes selected"}

    # 1. Ensure the target has UVs, unless the user is managing them externally.
    if not _ensure_uv(
        target_result,
        method=uv_method,
        island_margin=uv_island_margin,
        auto_unwrap=auto_unwrap,
        profile=uv_profile,
        texture_size=texture_size,
        margin_px=uv_margin_px,
        preserve_existing_seams=uv_preserve_seams,
    ):
        return {
            "success": False,
            "images": [],
            "error": f"'{target_result.name}' has no UV map. Enable Auto Unwrap or unwrap the target first.",
        }

    # 1b. Recalculate normals on the target if requested
    if recalc_normals:
        bpy.context.view_layer.objects.active = target_result
        target_result.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode="OBJECT")

    # 2. Create world-space copies of the originals for baking (sources).
    source_objects = source_original if isinstance(source_original, (list, tuple)) else [source_original]
    temp_sources = []
    for index, source in enumerate(source_objects):
        temp_source = _make_world_space_copy(source, f"_bake_source_tmp_{index}")
        temp_sources.append(temp_source)

    # 3. Create blank images (use final_name for clean naming)
    images = _create_bake_images(img_base, texture_size, passes)

    # 4. Build material on target with image nodes
    channels = _build_bake_material(target_result, images)
    bake_mat = bpy.data.materials.get(f"{target_result.name}_baked")
    if bake_mat is None:
        for temp_source in temp_sources:
            bpy.data.objects.remove(temp_source, do_unlink=True)
        return {"success": False, "images": [], "error": "Could not create baked material"}

    # 5. Set up scene for baking
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 128

    # Select source and make target active
    bpy.ops.object.select_all(action="DESELECT")
    for temp_source in temp_sources:
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
    bake_st.use_cage = cage_extrusion > 0
    bake_st.cage_extrusion = cage_extrusion
    bake_st.max_ray_distance = max_ray_distance

    # In Blender 5.1, the bake TYPE is passed directly to the operator,
    # not set on BakeSettings (which only accepts NORMALS/DISPLACEMENT).
    # Blender 5.1 valid bake types:
    # COMBINED, AO, SHADOW, POSITION, NORMAL, UV, ROUGHNESS, EMIT,
    # ENVIRONMENT, DIFFUSE, GLOSSY, TRANSMISSION
    bake_configs = [
        ("roughness", "ROUGHNESS"),
        ("normal", "NORMAL"),
        ("ao", "AO"),
        # Albedo is last because this pass temporarily replaces the source
        # surface shader with emission to bypass Metallic.
        ("diffuse", "EMIT"),
    ]

    # ── Half-scale ──────────────────────────────────────────────
    # Temporarily set both objects to 0.5× scale (no transform apply).
    # This shrinks the absolute surface displacement so bake rays hit
    # reliably.  The target's scale is restored after baking.
    _half = bpy.context.scene.remi_settings.bake_half_scale
    if _half:
        _t_save = target_result.scale.copy()
        for temp_source in temp_sources:
            temp_source.scale = (0.5, 0.5, 0.5)
        target_result.scale = (0.5, 0.5, 0.5)

    bake_error = None
    try:
        for channel, bake_type in bake_configs:
            if channel not in passes:
                continue
            if channel == "diffuse":
                for temp_source in temp_sources:
                    _prepare_albedo_emission(temp_source)
            node = channels[channel]
            bake_mat.node_tree.nodes.active = node
            node.select = True
            bpy.ops.object.bake(type=bake_type)
    except RuntimeError as error:
        bake_error = str(error)
    finally:
        # Always leave the scene usable after a failed bake.
        if _half:
            target_result.scale = _t_save
        bpy.ops.object.select_all(action="DESELECT")
        for temp_source in temp_sources:
            if bpy.data.objects.get(temp_source.name) is not None:
                bpy.data.objects.remove(temp_source, do_unlink=True)
        scene.render.engine = prev_engine

    if bake_error:
        return {"success": False, "images": [], "error": f"Bake failed: {bake_error}"}

    image_names = list(images.keys())
    print(f"Baking: Done — created {image_names}")

    return {
        "success": True,
        "images": image_names,
    }
