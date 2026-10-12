"""Run in factory-startup Blender; private bake copies must retain face materials."""
from pathlib import Path
import sys
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from remi.features.bake.engine import _make_world_space_copy, _remove_temp_object

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
source = bpy.context.object
for name, color in [('Red', (1, 0, 0, 1)), ('Green', (0, 1, 0, 1))]:
    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    source.data.materials.append(material)
for face in source.data.polygons:
    face.material_index = face.index % 2
indices = [face.material_index for face in source.data.polygons]
materials = list(source.data.materials)
source.scale = (2, 3, 4)
copy = _make_world_space_copy(source, 'PrivateBakeCopy')
assert [face.material_index for face in copy.data.polygons] == indices
assert [face.material_index for face in source.data.polygons] == indices
assert tuple(copy.scale) == (1, 1, 1)
for index, original in enumerate(materials):
    assert copy.data.materials[index] != original
    assert tuple(copy.data.materials[index].diffuse_color) == tuple(original.diffuse_color)
_remove_temp_object(copy)
assert all(material.name in bpy.data.materials for material in materials)
print('REMI_BAKE_MATERIAL_SLOTS_OK')
