"""Scene-only LOD geometry with the original materials, images and UV layers.

Use Blender's native collapse reducer so no OBJ round-trip can discard UV
channels. UV/material/geometry boundaries are pinned; texture IDs are shared,
never copied, baked, packed, resized or written to disk.
"""

import math
import uuid

import bmesh
import bpy


def draw_settings(layout, settings):
    for name in ("lod_levels", "lod_ratio"):
        layout.prop(settings, name)
    layout.label(text="Reuse ALL original materials and texture maps", icon="IMAGE_DATA")
    layout.label(text="Geometry only · no baking, resizing or export")
    layout.label(text="Source is LOD0; reduced copies are hidden initially")


def _triangles(obj):
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def _snapshot():
    # No material/image IDs should be allocated by this feature at all.
    return {key: set(getattr(bpy.data, key)) for key in ("objects", "meshes", "collections")}


def _discard_new(before):
    created = [item for key, values in before.items() for item in set(getattr(bpy.data, key))-values]
    if created:
        bpy.data.batch_remove(ids=created)


def _evaluated_copy(context, source, collection):
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated,
        preserve_all_data_layers=True, depsgraph=depsgraph)
    # Evaluated material pointers must not escape the depsgraph. Resolve back
    # to the exact live source materials, including object-linked overrides.
    for index, slot in enumerate(evaluated.material_slots):
        mesh.materials[index] = slot.material.original if slot.material else None
    obj = bpy.data.objects.new(source.name + " · LOD work", mesh)
    collection.objects.link(obj)
    obj.matrix_world = source.matrix_world.copy()
    for group in source.vertex_groups:
        if obj.vertex_groups.get(group.name) is None:
            obj.vertex_groups.new(name=group.name)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj
    modifier = obj.modifiers.new("LOD triangulation", "TRIANGULATE")
    if hasattr(modifier, "keep_custom_normals"):
        modifier.keep_custom_normals = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    return obj


def _locked_vertices(mesh):
    """Pin chart borders in EVERY UV channel, material borders and sharp edges.

    Unlike a geometry-only cost with a nominal UV delimiter, a zero-weight
    vertex actually prevents Blender's collapse reducer from moving/collapsing
    edges incident to that vertex. Intentional overlaps, mirrored/tiled UVs,
    UDIM coordinates and disconnected UV islands are never repacked.
    """
    for layer in mesh.uv_layers:
        if any(not math.isfinite(value) for loop in layer.data for value in loop.uv):
            raise ValueError(f"UV map '{layer.name}' contains non-finite coordinates")
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.index_update()
        layers = list(bm.loops.layers.uv.values())
        locked = set()
        for edge in bm.edges:
            protect = (not edge.is_manifold or edge.seam or not edge.smooth)
            if not protect:
                a, b = edge.link_loops
                protect = a.face.material_index != b.face.material_index
                if not protect:
                    for layer in layers:
                        for vertex in edge.verts:
                            la = a if a.vert == vertex else a.link_loop_next
                            lb = b if b.vert == vertex else b.link_loop_next
                            if (la[layer].uv-lb[layer].uv).length_squared > 1.0e-14:
                                protect = True
                                break
                        if protect:
                            break
            if protect:
                locked.update(vertex.index for vertex in edge.verts)
        return locked
    finally:
        bm.free()


def _reduce(context, base, source, collection, ratio, level, locked):
    obj = bpy.data.objects.new(f"{source.name}_LOD{level}", base.data.copy())
    collection.objects.link(obj)
    obj.matrix_world = base.matrix_world.copy()
    for group in base.vertex_groups:
        if obj.vertex_groups.get(group.name) is None:
            obj.vertex_groups.new(name=group.name)
    group = obj.vertex_groups.new(name="_remi_lod_free_" + uuid.uuid4().hex[:8])
    group_name = group.name
    group.add(list(range(len(obj.data.vertices))), 0.0, "REPLACE")
    free = [vertex.index for vertex in obj.data.vertices if vertex.index not in locked]
    if free:
        group.add(free, 1.0, "REPLACE")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj
    modifier = obj.modifiers.new("LOD geometry reduction", "DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    modifier.vertex_group = group_name
    modifier.vertex_group_factor = 1.0
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    group = obj.vertex_groups.get(group_name)
    if group is not None:
        obj.vertex_groups.remove(group)
    if [layer.name for layer in obj.data.uv_layers] != [layer.name for layer in base.data.uv_layers]:
        raise RuntimeError("LOD reduction lost a source UV channel; no result was kept")
    if list(obj.data.materials) != list(base.data.materials):
        raise RuntimeError("LOD reduction changed the source materials; no result was kept")
    # Preserve active/edit/render channel choices, not just channel names.
    if base.data.uv_layers.active:
        obj.data.uv_layers.active_index = base.data.uv_layers.active_index
        for original, layer in zip(base.data.uv_layers, obj.data.uv_layers):
            layer.active_render = original.active_render
    obj["remi_lod_level"] = level
    obj["remi_lod_source"] = source
    obj.hide_render = True
    obj.hide_set(True)
    return obj


def create_lods(source, *, levels=3, ratio=0.5, progress=None):
    """Create reduced scene objects, sharing all original material/image IDs.

    Accept one mesh or multipart sources. Source object(s) serve as LOD0 and
    remain untouched. Each reduced level starts from evaluated LOD0 geometry,
    never from a previous LOD. Results are grouped and hidden to avoid overlap.
    This function creates no asset directories or delivery files.
    """
    if isinstance(levels, bool) or not isinstance(levels, int) or not 1 <= levels <= 6:
        raise ValueError("Choose between 1 and 6 reduced LODs")
    if not math.isfinite(ratio) or not 0.05 <= ratio <= 0.95:
        raise ValueError("Choose a Keep per Level fraction between 0.05 and 0.95")
    sources = list(source) if isinstance(source, (list, tuple)) else [source]
    if not sources or any(obj is None or obj.type != "MESH" or not obj.data.polygons for obj in sources):
        raise ValueError("Choose mesh objects with faces")
    context = bpy.context
    if context.mode != "OBJECT":
        raise ValueError("Switch to Object Mode before creating LODs")
    if any(obj.name not in context.view_layer.objects for obj in sources):
        raise ValueError("The LOD source must be in the active view layer")
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    before = _snapshot()
    status = progress or (lambda phase, fraction: None)
    bases, objects, reports = [], [], []
    try:
        collection = bpy.data.collections.new(sources[0].name + " · LODs")
        context.scene.collection.children.link(collection)
        collection["remi_lod_source"] = sources[0]
        status("Preparing source geometry (original textures shared)", 0.0)
        for obj in sources:
            base = _evaluated_copy(context, obj, collection)
            bases.append((obj, base, _locked_vertices(base.data)))
        initial_count = sum(_triangles(base) for _, base, _ in bases)
        reports.append({"level": 0, "triangles": initial_count, "objects": sources})
        for level in range(1, levels+1):
            status(f"Creating LOD{level} (same original texture maps)", (level-1)/levels)
            parts = [_reduce(context, base, original, collection, ratio**level, level, locked)
                     for original, base, locked in bases]
            count = sum(_triangles(obj) for obj in parts)
            if count >= reports[-1]["triangles"]:
                hint = ("UV/material seams or boundaries pin the remaining vertices" if level == 1
                        else "use fewer levels")
                raise RuntimeError(f"LOD{level} cannot reduce further while preserving UV/material seams; {hint}")
            objects.extend(parts)
            reports.append({"level": level, "triangles": count,
                            "target_triangles": max(1, int(initial_count*ratio**level)), "objects": parts})
        for _, base, _ in bases:
            mesh = base.data
            bpy.data.objects.remove(base, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        status("LODs created in Blender; all original maps shared", 1.0)
        return {"sources": sources, "objects": objects, "collection": collection, "levels": reports}
    except Exception:
        _discard_new(before)
        raise
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in selected:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        context.view_layer.objects.active = active
