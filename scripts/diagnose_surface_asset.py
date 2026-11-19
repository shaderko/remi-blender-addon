"""Read-only diagnosis of real GLB source topology and adaptive fallback reasons.

Blender --background --factory-startup --python-exit-code 1 --python this.py --
--input <asset.glb> --output <folder> [--mesh 0 1 ...] [--save-source].
Exact-position welding below is a diagnostic comparison, never an asset edit.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
from remi.features.retopology.instant_meshes.adaptive import BVHSurface
from remi.features.retopology.instant_meshes.quality import mesh_quality, output_edges
from remi.features.retopology.instant_meshes.runtime import runtime
from remi.features.retopology.instant_meshes.surface_adaptive import SurfacePatchRemesher, _single_vertex_fans


def boundary_junctions(vertices, faces):
    edges, counts, _sizes = output_edges(faces)
    degree = np.bincount(edges[counts == 1].ravel(), minlength=len(vertices))
    return int(np.sum((degree != 0) & (degree != 2)))


def single_fans(faces):
    iterator = _single_vertex_fans(faces, lambda: None)
    while True:
        try:
            next(iterator)
        except StopIteration as result:
            return result.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mesh", type=int, nargs="*")
    parser.add_argument("--save-source", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    original_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=str(args.input))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    report = {"input": str(args.input), "sha256": original_hash, "mesh_count": len(meshes), "meshes": []}
    for index, obj in enumerate(meshes):
        if args.mesh is not None and index not in args.mesh:
            continue
        vertices, faces = runtime._evaluated_triangles(obj)
        unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
        welded_faces = inverse[faces].astype(np.int32)
        raw = mesh_quality(vertices, faces)
        welded = mesh_quality(unique, welded_faces)
        tree = BVHTree.FromPolygons([Vector(v) for v in vertices], faces.tolist(), all_triangles=True)
        # Deliberately non-result sentinel: this tool diagnoses rejection paths,
        # not the native comparison solve or final retopology quality.
        sentinel = (np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0]], np.float32), np.array([[0,1,2,3]], np.int32))
        task = SurfacePatchRemesher(vertices, faces, BVHSurface(tree), fallback=sentinel,
                                   error=max(float(np.linalg.norm(np.ptp(vertices, axis=0)))*.005, 1e-12), max_seconds=3)
        for _ in task.run():
            pass
        item = {"index": index, "name": obj.name, "raw": raw, "exact_position_comparison": welded,
                "raw_boundary_junctions": boundary_junctions(vertices, faces),
                "comparison_boundary_junctions": boundary_junctions(unique, welded_faces),
                "comparison_single_vertex_fans": single_fans(welded_faces), "adaptive": task.report}
        report["meshes"].append(item)
        if args.save_source:
            np.savez_compressed(args.output / f"source-{index}.npz", vertices=vertices, faces=faces)
        print("REMI_ASSET_SURFACE_DIAG", index, "triangles", len(faces),
              "raw-components", raw["components"], "geometric-components", welded["components"], task.report["reason"], flush=True)
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    assert hashlib.sha256(args.input.read_bytes()).hexdigest() == original_hash, "The original asset was changed"
    print("REMI_ASSET_SURFACE_DIAG_OK", args.output / "report.json", flush=True)


if __name__ == "__main__":
    main()
