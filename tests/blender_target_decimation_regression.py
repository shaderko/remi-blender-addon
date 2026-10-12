"""Real absolute-target reduction and saved-node voxel-limit regression.

Source checkouts can set REMI_TEST_DEPENDENCIES to their isolated bundled-wheel
site directory. Installed packages already provide PyMeshLab.
"""
import sys
from pathlib import Path
import bpy
import os
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
if os.environ.get('REMI_TEST_DEPENDENCIES'):
    dependencies=Path(os.environ['REMI_TEST_DEPENDENCIES'])
    sys.path.insert(0,str(dependencies))
    for kind in ('purelib','platlib'):
        for path in dependencies.glob('*.data/'+kind):sys.path.insert(0,str(path))
import pymeshlab
import remi as r
r.register()
from remi.features.remesh.decimation import create_candidate
from remi.features.remesh.geometry_nodes import ensure_remi_node_group
s=bpy.context.scene.remi_settings;tree=ensure_remi_node_group()
voxel=next(i for i in tree.interface.items_tree if i.name=='Voxel Size');voxel.min_value=.001
assert next(i for i in ensure_remi_node_group().interface.items_tree if i.name=='Voxel Size').min_value<1/1024
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=6,radius=.5)
source=bpy.context.object;s.decimation_target_faces=2000;s.decimation_passes=20;s.target_percentage=.5
result,error,passes=create_candidate(source,s);assert not error,error;result.data.calc_loop_triangles();assert 1980<=len(result.data.loop_triangles)<=2020
for p in passes:assert p['output_faces']>=p['input_faces']*.5-2
assert len(passes)<20
print('REMI_TARGET_AND_SAVED_NODE_LIMIT_OK',len(result.data.vertices),len(result.data.loop_triangles),len(passes));r.unregister()
