"""
Standalone worker: runs PyMeshLab decimation as a subprocess.
Called by the modal operator so Blender doesn't block.
"""
import sys
import os
import json

# Add user site-packages for pymeshlab
import site
sp = site.getusersitepackages()
if sp and sp not in sys.path:
    sys.path.insert(0, sp)

import pymeshlab

# Args: input_ply output_ply target_percentage passes
input_path = sys.argv[1]
output_path = sys.argv[2]
target_perc = float(sys.argv[3])
passes = int(sys.argv[4])

ms = pymeshlab.MeshSet()
ms.load_new_mesh(input_path)
orig_faces = ms.current_mesh().face_number()

for i in range(passes):
    ms.apply_filter("meshing_decimation_quadric_edge_collapse",
                    targetperc=target_perc, autoclean=True)
    current = ms.current_mesh().face_number()
    # Write progress to stdout as JSON lines
    print(json.dumps({"pass": i + 1, "passes": passes,
                       "in_faces": orig_faces,
                       "out_faces": current}))
    sys.stdout.flush()

ms.save_current_mesh(output_path, binary=True)
print(json.dumps({"done": True, "output": output_path}))
sys.stdout.flush()
