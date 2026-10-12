"""Native UV conditioning must retain small valid faces, across object units."""
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from remi.features.uv.engine.packing import _condition_coordinates
from remi.features.uv.engine._native import pack_uvs, unwrap_mesh, uv_overlaps

uv = np.array([[0,0],[1,0],[0,1],[2,0],[2.0001,0],[2,.0001]], dtype=np.float32)
tri = np.array([[0,1,2],[3,4,5]], dtype=np.uint32)
charts = np.array([0,1], dtype=np.uint32)
def pack(points):
    return pack_uvs(points,tri,charts,512,4,False,True,True,True,False)
# Demonstrate the real native failure, rather than testing only the adapter.
assert np.any(pack(uv)['atlas_indices'] < 0)
for units in (1e-4, 1, 1e4):
    source = uv * units
    original = source.copy()
    conditioned = _condition_coordinates(source,tri)
    assert np.array_equal(source, original)
    # Uniform scaling cannot change relative face areas or chart proportions.
    np.testing.assert_allclose(conditioned / conditioned[1,0], uv, atol=2e-7)
    result = pack(conditioned)
    assert result['atlas_count'] == 1 and np.all(result['atlas_indices'] == 0)
    assert not len(uv_overlaps(result['uvs'],tri,0)['pairs'])
    positions = np.column_stack((source,np.zeros(len(source)))).astype(np.float32)
    result = unwrap_mesh(_condition_coordinates(positions,tri),tri,charts,512,4,2.,2,False,True)
    assert result['atlas_count'] == 1 and np.all(result['triangle_atlas_indices'] == 0)
print('REMI_SMALL_FACE_UV_REGRESSION_OK')
