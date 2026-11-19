"""Probe field-layout topology guards per density on a real cached variant."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
from remi.features.retopology.instant_meshes._native import Session
from remi.features.retopology.instant_meshes.quality import mesh_quality
from remi.features.retopology.instant_meshes.surface_topology import stitch_render_seams, consistent_winding
from remi.features.retopology.instant_meshes.surface_adaptive import _single_vertex_fans


def finish(iterator):
    while True:
        try:
            next(iterator)
        except StopIteration as result:
            return result.value


parser = argparse.ArgumentParser()
parser.add_argument("--cache", type=Path, required=True)
parser.add_argument("--mesh", type=int, required=True)
parser.add_argument("--target", type=int, nargs="+", default=[256, 512, 1024, 2048])
args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
archive = np.load(args.cache / f"source-{args.mesh}.npz")
vertices, faces, preparation = finish(stitch_render_seams(archive["vertices"], archive["faces"]))
quality = mesh_quality(vertices, faces)
print("SOURCE", quality, flush=True)
for target in args.target:
    session = Session(vertices.astype(np.float32), faces, target_faces=target, pure_quad=True,
                      crease_angle=35, extrinsic=False, align_boundaries=True, deterministic=True,
                      smooth_iterations=2)
    session.start_orientation()
    while session.active:
        time.sleep(.002)
    session.start_position()
    while session.active:
        time.sleep(.002)
    output, result, _normals = session.extract()
    session.stop()
    output = np.asarray(output)
    result = np.asarray(result, np.int32)
    if result.shape[1] == 4:
        result = result[result[:, 2] != result[:, 3]]
    q = mesh_quality(output, result)
    ideal = 1-q["irregular_vertices"]/max(q["vertices"], 1)
    fans = finish(_single_vertex_fans(result, lambda: None))
    print("TARGET", target, "faces", len(result), "ideal", round(ideal, 3),
          "aspect", round(q["aspect_p95"], 2), "angle", round(q["angle_error_p95_deg"], 1),
          "boundary", q["boundary_edges"], "nonmanifold", q["nonmanifold_edges"],
          "degenerate", q["degenerate_faces"], "euler", q["euler_characteristic"],
          "components", q["components"], "winding", consistent_winding(result), "fans", fans, flush=True)
print("REMI_FIELD_GUARD_PROBE_OK", flush=True)
