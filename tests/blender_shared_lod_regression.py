"""Scene LODs: exact original maps, all UV channels, no baking/files/exports."""

from unittest import mock

import bpy
import numpy as np
import remi

from remi.features.remesh import lods
from remi.features.bake import engine as baking
from remi.blender import mesh_exchange


remi.register()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=32, y_subdivisions=32, size=2)
source = bpy.context.object
source.name = "Original mesh"
source.location, source.scale = (2, -1, .3), (1.2, .8, 1.0)
source.rotation_euler = (.1, -.2, .3)
bpy.ops.object.shade_smooth()

albedo = bpy.data.images.new("Original albedo", 256, 256)
albedo.generated_color = (.2, .6, .9, 1)
normal = bpy.data.images.new("Original normal", 256, 256)
normal.generated_color = (.6, .4, .97, 1)
normal.colorspace_settings.name = "Non-Color"
roughness = bpy.data.images.new("Original roughness", 256, 256)
roughness.generated_color = (.4, .4, .4, 1)
uv0 = source.data.uv_layers.active
uv0.name = "Albedo UV"
uv1 = source.data.uv_layers.new(name="Normal UV")
for loop in source.data.loops:
    u, v = uv0.data[loop.index].uv
    # A different, mirrored/repeating UV channel must be kept, not rejected or
    # unwrapped because it is outside an atlas baker's preferred 0..1 range.
    uv1.data[loop.index].uv = (1.5-2*u, -.3+1.6*v)
source.data.uv_layers.active = uv1
uv0.active_render = True

materials = []
for name in ("Original left material", "Original right material"):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    bsdf = nodes["Principled BSDF"]
    for image, socket in ((albedo, "Base Color"), (roughness, "Roughness")):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        links.new(tex.outputs["Color"], bsdf.inputs[socket])
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = normal
    uv = nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv1.name
    links.new(uv.outputs["UV"], tex.inputs["Vector"])
    nmap = nodes.new("ShaderNodeNormalMap")
    nmap.uv_map = uv1.name
    links.new(tex.outputs["Color"], nmap.inputs["Color"])
    links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    source.data.materials.append(material)
    materials.append(material)
source.data.materials.append(None)  # unused empty slots must survive too
for face in source.data.polygons:
    face.material_index = int(face.center.x > 0)
source.data.update()

original_uv = [[tuple(loop.uv) for loop in layer.data] for layer in source.data.uv_layers]
original_indices = [face.material_index for face in source.data.polygons]
original_mesh = source.data
images_before, materials_before = set(bpy.data.images), set(bpy.data.materials)
groups_before = set(bpy.data.node_groups)
states = {image: (tuple(image.size), image.filepath, image.source, image.packed_file,
                  image.colorspace_settings.name) for image in (albedo, normal, roughness)}

with mock.patch.object(baking, "bake_textures", side_effect=AssertionError("LOD baking is forbidden")), \
     mock.patch.object(mesh_exchange, "export_obj_for_tool", side_effect=AssertionError("No OBJ export")), \
     mock.patch("zipfile.ZipFile", side_effect=AssertionError("No automatic ZIP export")), \
     mock.patch("pathlib.Path.mkdir", side_effect=AssertionError("No output folders")):
    result = lods.create_lods(source, levels=3, ratio=.5)

assert result["sources"] == [source]
assert len(result["objects"]) == 3
assert len(result["collection"].objects) == 3
assert [entry["triangles"] for entry in result["levels"]] == sorted(
    [entry["triangles"] for entry in result["levels"]], reverse=True)
assert result["levels"][-1]["triangles"] < result["levels"][0]["triangles"]*.3
assert source.data is original_mesh and source.select_get()
assert bpy.context.view_layer.objects.active is source
assert not source.hide_get() and not source.hide_render
assert [[tuple(loop.uv) for loop in layer.data] for layer in source.data.uv_layers] == original_uv
assert [face.material_index for face in source.data.polygons] == original_indices
assert set(bpy.data.images) == images_before
assert set(bpy.data.materials) == materials_before
assert set(bpy.data.node_groups) == groups_before
for image, state in states.items():
    assert (tuple(image.size), image.filepath, image.source, image.packed_file,
            image.colorspace_settings.name) == state

for level, obj in enumerate(result["objects"], 1):
    assert obj["remi_lod_level"] == level and obj["remi_lod_source"] == source
    assert obj.hide_get() and obj.hide_render
    assert list(obj.data.materials) == materials + [None]
    assert [layer.name for layer in obj.data.uv_layers] == ["Albedo UV", "Normal UV"]
    assert obj.data.uv_layers.active_index == source.data.uv_layers.active_index
    assert [layer.active_render for layer in obj.data.uv_layers] == [True, False]
    assert np.allclose(np.asarray(obj.matrix_world), np.asarray(source.matrix_world))
    assert {face.material_index for face in obj.data.polygons} == {0, 1}
    assert not obj.modifiers
    for face in obj.data.polygons:
        assert face.material_index == int(face.center.x > 0)
    # Both UV channels retain their source mapping; no atlas substitution.
    for loop in obj.data.loops:
        point = obj.data.vertices[loop.vertex_index].co
        u, v = point.x/2+.5, point.y/2+.5
        assert np.allclose(obj.data.uv_layers[0].data[loop.index].uv, (u, v), atol=2e-5)
        assert np.allclose(obj.data.uv_layers[1].data[loop.index].uv, (1.5-2*u, -.3+1.6*v), atol=2e-5)
    # Boundary and material-seam vertices stay exactly in place.
    coordinates = {tuple(round(value, 6) for value in vertex.co) for vertex in obj.data.vertices}
    for vertex in source.data.vertices:
        x, y, _ = vertex.co
        if abs(x) == 1 or abs(y) == 1 or abs(x) < 1e-7:
            assert tuple(round(value, 6) for value in vertex.co) in coordinates

# A failure removes only new geometry/collections, never original maps or data.
before = lods._snapshot()
with mock.patch.object(lods, "_reduce", side_effect=RuntimeError("deliberate failure")):
    try:
        lods.create_lods(source, levels=1)
    except RuntimeError as exc:
        assert "deliberate failure" in str(exc)
    else:
        raise AssertionError("Failed LOD generation was published")
assert lods._snapshot() == before
assert set(bpy.data.images) == images_before and set(bpy.data.materials) == materials_before

# Object-linked shader overrides must resolve to the same live material, not
# the underlying mesh slot or an evaluated/depsgraph-only material pointer.
source.material_slots[1].link = "OBJECT"
source.material_slots[1].material = materials[0]
weights = source.vertex_groups.new(name="Artist weights")
weights.add(list(range(len(source.data.vertices))), .75, "REPLACE")
overrides = lods.create_lods(source, levels=1)
assert list(overrides["objects"][0].data.materials) == [materials[0], materials[0], None]
assert list(source.data.materials) == materials + [None]
assert [group.name for group in overrides["objects"][0].vertex_groups] == ["Artist weights"]
assert set(bpy.data.images) == images_before and set(bpy.data.materials) == materials_before

# Multipart sources keep distinct materials and share their existing images.
bpy.ops.mesh.primitive_grid_add(x_subdivisions=2, y_subdivisions=2, size=.2)
small = bpy.context.object
small.data.materials.append(materials[1])
multipart = lods.create_lods([source, small], levels=2)
assert len(multipart["objects"]) == 4
assert list(multipart["objects"][1].data.materials) == [materials[1]]
assert set(bpy.data.images) == images_before
print("REMI_SHARED_LOD_REGRESSION_OK", [entry["triangles"] for entry in result["levels"]], flush=True)
