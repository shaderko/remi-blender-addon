"""Verify neutral SDF extraction, legacy offset, and higher detail on geometry."""
from pathlib import Path
import sys
import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import remi
from remi.features.remesh.service import create_candidate

remi.register()
try:
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96,ring_count=48,radius=.5)
    source=bpy.context.object
    settings=bpy.context.scene.remi_settings
    assert settings.voxel_surface_offset==1.0, 'Preserve the standalone legacy default'
    settings.remesh_backend='VOXEL';settings.use_hole_repair=False
    measured={}
    for name,resolution,offset in [('legacy',128,1),('neutral',128,0),('fine',256,0)]:
        settings.voxel_size=1/resolution;settings.voxel_surface_offset=offset
        obj,error,report=create_candidate(source,settings,apply_result=True)
        assert not error,error
        obj.data.calc_loop_triangles()
        radii=np.array([v.co.length for v in obj.data.vertices])
        measured[name]=(float(np.median(radii)),len(obj.data.loop_triangles))
        assert report['surface_offset_voxels']==offset
        bpy.data.objects.remove(obj,do_unlink=True)
    assert measured['legacy'][0] > measured['neutral'][0] + .005, measured
    assert abs(measured['fine'][0]-.5)<.001, measured
    assert measured['fine'][1]>measured['neutral'][1]*3, measured
    print('REMI_VOXEL_SURFACE_REGRESSION_OK',measured)
finally:
    remi.unregister()
