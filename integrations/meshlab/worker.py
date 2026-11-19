"""Run texture-preserving PyMeshLab decimation outside Blender's process."""

import json
import sys

import pymeshlab

# Args: input_path output_path target_percentage passes preserve_detail
#       [preserve_texture]
#       [target_faces]
input_path = sys.argv[1]
output_path = sys.argv[2]
target_perc = float(sys.argv[3])
passes = int(sys.argv[4])
preserve_detail = len(sys.argv) > 5 and sys.argv[5].lower() in {"1", "true", "yes"}
preserve_texture = len(sys.argv) > 6 and sys.argv[6].lower() in {"1", "true", "yes"}
target_faces = int(sys.argv[7]) if len(sys.argv) > 7 else 0

ms = pymeshlab.MeshSet()
ms.load_new_mesh(input_path)

for pass_index in range(passes):
    before = ms.current_mesh().face_number()
    if target_faces and before <= target_faces:
        break
    filter_name = (
        "meshing_decimation_quadric_edge_collapse_with_texture"
        if preserve_texture
        else "meshing_decimation_quadric_edge_collapse"
    )
    filter_args = {
        "preservenormal": preserve_detail,
        "planarquadric": preserve_detail,
    }
    if target_faces:
        filter_args.update(targetperc=0.0, targetfacenum=target_faces)
    else:
        filter_args["targetperc"] = target_perc
    if not preserve_texture:
        filter_args["autoclean"] = True
    ms.apply_filter(filter_name, **filter_args)
    current = ms.current_mesh().face_number()
    # Write progress to stdout as JSON lines
    print(
        json.dumps(
            {
                "pass": pass_index + 1,
                "passes": passes,
                "in_faces": before,
                "out_faces": current,
            }
        )
    )
    sys.stdout.flush()
    if current >= before:
        break

if preserve_texture:
    ms.save_current_mesh(output_path)
else:
    ms.save_current_mesh(output_path, binary=True)
print(json.dumps({"done": True, "output": output_path}))
sys.stdout.flush()
