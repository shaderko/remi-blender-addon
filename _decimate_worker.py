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

# Args: input_path output_path target_percentage passes preserve_detail
#       [preserve_texture]
input_path = sys.argv[1]
output_path = sys.argv[2]
target_perc = float(sys.argv[3])
passes = int(sys.argv[4])
preserve_detail = len(sys.argv) > 5 and sys.argv[5].lower() in {"1", "true", "yes"}
preserve_texture = len(sys.argv) > 6 and sys.argv[6].lower() in {"1", "true", "yes"}

ms = pymeshlab.MeshSet()
ms.load_new_mesh(input_path)
orig_faces = ms.current_mesh().face_number()

for i in range(passes):
    filter_name = (
        "meshing_decimation_quadric_edge_collapse_with_texture"
        if preserve_texture
        else "meshing_decimation_quadric_edge_collapse"
    )
    filter_args = {
        "targetperc": target_perc,
        "preservenormal": preserve_detail,
        "planarquadric": preserve_detail,
    }
    if not preserve_texture:
        filter_args["autoclean"] = True
    ms.apply_filter(filter_name, **filter_args)
    current = ms.current_mesh().face_number()
    # Write progress to stdout as JSON lines
    print(json.dumps({"pass": i + 1, "passes": passes,
                       "in_faces": orig_faces,
                       "out_faces": current}))
    sys.stdout.flush()

if preserve_texture:
    ms.save_current_mesh(output_path)
else:
    ms.save_current_mesh(output_path, binary=True)
print(json.dumps({"done": True, "output": output_path}))
sys.stdout.flush()
