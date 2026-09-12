"""
Texture baking for Remi.
Bakes albedo, roughness, normal, and ambient-occlusion maps from the original
high-poly mesh onto the remeshed/decimated result.
"""

import math

import bpy
from mathutils import Vector

from ..uv.engine import ensure_remi_uv
from ..uv.engine.blender_bridge import validate_existing_uv


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
    if obj.data.uv_layers:
        # Reuse unchanged verified maps, but never confuse layer existence with
        # validity. This does not regenerate or alter artist UVs that pass.
        stats = validate_existing_uv(obj)
        if stats is not None and stats.valid:
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


def _prepare_world_space_object(obj: bpy.types.Object, name: str) -> bpy.types.Object:
    """Make a disposable object safe for baking and apply its world transform."""
    source_materials = list(obj.data.materials)
    obj.data.materials.clear()
    for material in source_materials:
        obj.data.materials.append(material.copy() if material else None)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # Apply modifiers (iterate in reverse since applying removes them)
    for mod in list(obj.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=mod.name)
        except Exception:
            pass
    # Bake transform into vertices
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    obj.name = name
    return obj


def _make_world_space_copy(obj: bpy.types.Object, name: str) -> bpy.types.Object:
    """Create a duplicate with all modifiers + transform applied (world-space)."""
    dup = obj.copy()
    dup.data = obj.data.copy()
    bpy.context.collection.objects.link(dup)
    try:
        return _prepare_world_space_object(dup, name)
    except Exception:
        _remove_temp_object(dup)
        raise


def _remove_temp_object(obj: bpy.types.Object):
    """Remove a disposable bake object and its private mesh/material data."""
    if obj is None:
        return
    try:
        mesh = obj.data if obj.type == "MESH" else None
        materials = list(mesh.materials) if mesh else []
        bpy.data.objects.remove(obj, do_unlink=True)
    except ReferenceError:
        return
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    for material in materials:
        if material and material.users == 0:
            bpy.data.materials.remove(material)


def _world_bbox_diagonal(obj: bpy.types.Object) -> float:
    """Length of the object's world-space bounding-box diagonal."""
    matrix = obj.matrix_world
    corners = [matrix @ Vector(corner) for corner in obj.bound_box]
    spans = [
        max(corner[axis] for corner in corners) - min(corner[axis] for corner in corners)
        for axis in range(3)
    ]
    return sum(span * span for span in spans) ** 0.5


def _sample_surface_points(obj: bpy.types.Object, limit: int = 1500):
    """Return up to 'limit' evenly spaced world-space vertices of the object."""
    vertices = obj.data.vertices
    total = len(vertices)
    if not total:
        return []
    step = max(1, -(-total // limit))
    matrix = obj.matrix_world
    return [matrix @ vertices[index].co for index in range(0, total, step)]


def _derive_bake_distances(target: bpy.types.Object, sources) -> tuple:
    """Size the cage/ray search to the real original-to-result gap.

    The stored defaults are absolute world-unit constants, so they mean
    nothing across model scales: a remesh sitting 0.5 units off a 9-unit
    sculpt bakes blank with 0.1, while the same value wildly overshoots a
    0.02-unit part. Measure how far the target surface actually sits from the
    originals and cover that, with a small proportional floor so a sparse
    sample cannot undercut the result.

    Cage rays start on the extruded cage and travel inward, so the extrusion
    has to clear the target-to-source gap. Max ray distance is the search
    length and has to cross the model, so it is tied to the bounding box.
    """
    diagonal = _world_bbox_diagonal(target)
    floor = 0.002 * diagonal
    gaps = []
    for source in sources:
        if source is None or source.type != "MESH":
            continue
        inverse = source.matrix_world.inverted()
        for point in _sample_surface_points(target):
            found, location, _normal, _index = source.closest_point_on_mesh(
                inverse @ point
            )
            if found:
                gaps.append((point - (source.matrix_world @ location)).length)
    if not gaps:
        return floor, diagonal
    gaps.sort()
    reach = max(2.0 * gaps[int(0.98 * (len(gaps) - 1))], floor)
    return reach, max(diagonal, 2.0 * reach)


def _create_bake_images(
    name_prefix: str,
    size: int,
    channels: tuple[str, ...],
    *,
    reuse_existing: bool = True,
) -> dict:
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
        img = bpy.data.images.get(image_name) if reuse_existing else None
        if img is None:
            img = bpy.data.images.new(name=image_name, width=size, height=size, alpha=True)
        elif img.size[0] != size or img.size[1] != size:
            img.scale(size, size)
        img.generated_color = color
        img.colorspace_settings.name = cs
        img.file_format = "PNG"
        images[key] = img
    return images


def _build_bake_material(
    obj: bpy.types.Object,
    images: dict,
    name_prefix: str = "",
    *,
    reuse_existing: bool = True,
):
    """Create or update Remi's baked material and return it with its image nodes."""
    material_name = f"{name_prefix or obj.name}_baked"
    mat = bpy.data.materials.get(material_name) if reuse_existing else None
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

    return channels, mat


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
    auto_cage: bool = False,
    passes: tuple[str, ...] = ("diffuse", "roughness", "normal", "ao"),
    consume_sources: bool = False,
    reuse_outputs: bool = True,
) -> dict:
    """Bake albedo, roughness, normal, and AO maps from source to target.

    Source and target meshes must overlap in world space. This function
    accepts one source or a list of source meshes, creates world-space copies
    for baking, then cleans them up. When ``consume_sources`` is true, the
    supplied objects are already disposable checkpoint loads and are prepared
    in place to avoid another high-poly mesh copy.

    Returns dict with keys 'success' and 'images' (list of created image names).
    """
    scene = bpy.context.scene
    prev_engine = scene.render.engine
    prev_cycles_samples = scene.cycles.samples

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
    try:
        for index, source in enumerate(source_objects):
            if consume_sources:
                temp_sources.append(source)
                temp_source = _prepare_world_space_object(source, f"_bake_source_tmp_{index}")
            else:
                temp_source = _make_world_space_copy(source, f"_bake_source_tmp_{index}")
                temp_sources.append(temp_source)
    except Exception:
        for temp_source in temp_sources:
            _remove_temp_object(temp_source)
        raise

    # 3. Create blank images (use final_name for clean naming)
    images = _create_bake_images(
        img_base,
        texture_size,
        passes,
        reuse_existing=reuse_outputs,
    )

    # 4. Build material on target with image nodes
    channels, bake_mat = _build_bake_material(
        target_result,
        images,
        img_base,
        reuse_existing=reuse_outputs,
    )
    if bake_mat is None:
        for temp_source in temp_sources:
            _remove_temp_object(temp_source)
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
    # Half-scale shrinks both meshes by the same factor, so the derived
    # distances have to shrink with them to stay in the same world space.
    _half = bpy.context.scene.remi_settings.bake_half_scale
    if auto_cage:
        cage_extrusion, max_ray_distance = _derive_bake_distances(
            target_result, temp_sources
        )
        if _half:
            cage_extrusion *= 0.5
            max_ray_distance *= 0.5
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
    # Shrink both meshes about the world origin by 0.5 so the absolute surface
    # displacement drops and bake rays hit reliably.  Crucially the shrink has
    # to be *relative*: _prepare_world_space_object normalises the source to
    # unit scale, but the target keeps whatever scale it arrived with.  Setting
    # the target's scale to a literal 0.5 (instead of multiplying it) leaves a
    # scaled target next to a half-sized source, so the two meshes no longer
    # line up and the rays miss.  Target transform is restored after baking.
    _t_save = None
    if _half:
        _t_save = (target_result.scale.copy(), target_result.location.copy())
        for temp_source in temp_sources:
            temp_source.scale = tuple(s * 0.5 for s in temp_source.scale)
            temp_source.location = tuple(l * 0.5 for l in temp_source.location)
        target_result.scale = tuple(s * 0.5 for s in _t_save[0])
        target_result.location = tuple(l * 0.5 for l in _t_save[1])

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
        if _half and _t_save is not None:
            target_result.scale = _t_save[0]
            target_result.location = _t_save[1]
        bpy.ops.object.select_all(action="DESELECT")
        for temp_source in temp_sources:
            try:
                exists = temp_source.name in bpy.data.objects
            except ReferenceError:
                exists = False
            if exists:
                _remove_temp_object(temp_source)
        scene.cycles.samples = prev_cycles_samples
        scene.render.engine = prev_engine

    if bake_error:
        return {"success": False, "images": [], "error": f"Bake failed: {bake_error}"}

    image_names = list(images.keys())
    print(f"Baking: Done — created {image_names}")

    return {
        "success": True,
        "images": image_names,
    }
