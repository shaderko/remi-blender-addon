"""Measure whether a quad mesh has good topology, not just few faces.

Blender --background --factory-startup --python-exit-code 1 --python this.py --
--cache <diagnosis source npz> --output <folder> --target 1200 2400 4800
[--adaptive <npz> ...]

Reports valence, scaled-Jacobian/mean-ratio quality, edge-ring length and
turning, and both sampled shape-error directions against the original surface.
"""

import argparse
import importlib.util
import json
import math
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
from remi.features.retopology.instant_meshes.adaptive import SAMPLE_WEIGHTS
from remi.features.retopology.instant_meshes.quality import mesh_quality, output_edges
from remi.features.retopology.instant_meshes.surface_adaptive import _output_trees, _reverse_error, _single_vertex_fans
from remi.features.retopology.instant_meshes.surface_topology import stitch_render_seams


def finish(iterator):
    while True:
        try:
            next(iterator)
        except StopIteration as result:
            return result.value


def quad_quality(vertices, faces):
    vertices = np.asarray(vertices, np.float64)
    quads = np.asarray(faces, np.int32)
    points = vertices[quads]
    edges = np.roll(points, -1, axis=1) - points
    lengths = np.linalg.norm(edges, axis=2)
    # Scaled Jacobian: minimum sine of the four corner angles.
    cross = np.cross(edges, np.roll(edges, -1, axis=1))
    face_normal = np.sum(np.cross(points, np.roll(points, -1, axis=1)), axis=1)
    face_normal /= np.maximum(np.linalg.norm(face_normal, axis=1)[:, None], 1e-30)
    sines = np.einsum("fij,fj->fi", cross, face_normal) / np.maximum(
        lengths * np.roll(lengths, -1, axis=1), 1e-30)
    jacobian = sines.min(axis=1)
    area = np.linalg.norm(np.cross(points[:, 2]-points[:, 0], points[:, 3]-points[:, 1]), axis=1)*.5
    mean_ratio = 4*area/np.maximum(np.sum(lengths**2, axis=1), 1e-30)
    angles = np.abs(np.degrees(np.arccos(np.clip(
        np.sum(edges * np.roll(edges, 1, axis=1), axis=2) /
        np.maximum(lengths * np.roll(lengths, 1, axis=1), 1e-30), -1, 1))) - 90)
    return {"scaled_jacobian_p05": float(np.quantile(jacobian, .05)),
            "scaled_jacobian_median": float(np.median(jacobian)),
            "mean_ratio_mean": float(np.mean(mean_ratio)),
            "mean_ratio_p05": float(np.quantile(mean_ratio, .05)),
            "angle_error_p95_deg": float(np.quantile(angles, .95)),
            "aspect_p95": float(np.quantile(lengths.max(axis=1)/np.maximum(lengths.min(axis=1), 1e-30), .95))}


def topology(vertices, faces):
    faces = np.asarray(faces, np.int32)
    quality = mesh_quality(vertices, faces)
    used = np.unique(faces)
    edges, counts, _ = output_edges(faces)
    degree = np.bincount(edges.ravel(), minlength=len(vertices))
    boundary = np.zeros(len(vertices), bool)
    boundary[edges[counts == 1].ravel()] = True
    interior = used[~boundary[used]]
    ideal = np.mean(degree[interior] == 4)
    valences = {str(v): int(np.sum(degree[interior] == v)) for v in (2, 3, 4, 5, 6)}
    valences["7+"] = int(np.sum(degree[interior] >= 7))
    # Edge rings: from an edge, cross the quad to its opposite edge.
    by_edge = {}
    for index, quad in enumerate(faces):
        for corner in range(4):
            edge = tuple(sorted((int(quad[corner]), int(quad[(corner+1) % 4]))))
            by_edge.setdefault(edge, []).append((index, corner))
    opposite = {}
    for edge, owners in by_edge.items():
        if len(owners) != 2:
            continue
        (a, ac), (b, bc) = owners
        opposite.setdefault((a, ac), (b, bc))
        opposite.setdefault((b, bc), (a, ac))
    seen, rings = set(), []
    for face in range(len(faces)):
        for corner in range(4):
            if (face, corner) in seen:
                continue
            ring, current = [], (face, corner)
            while current is not None and current not in seen:
                seen.add(current)
                ring.append(current)
                current = opposite.get(current)
            if len(ring) > 1:
                rings.append(ring)
    lengths = np.array([len(ring) for ring in rings], int)
    turning = []
    for ring in rings:
        points = [vertices[faces[face, [corner, (corner+1) % 4]]] for face, corner in ring]
        directions = np.array([b-a for a, b in points])
        directions /= np.maximum(np.linalg.norm(directions, axis=1)[:, None], 1e-30)
        turning.extend(np.degrees(np.arccos(np.clip(np.sum(directions[1:]*directions[:-1], axis=1), -1, 1))))
    turning = np.asarray(turning)
    return {
        **quality, **quad_quality(vertices, faces),
        "ideal_valence_fraction": float(ideal),
        "valences": valences,
        "edge_rings": len(rings),
        "ring_length_p50": float(np.median(lengths)) if len(lengths) else 0,
        "ring_length_p95": float(np.quantile(lengths, .95)) if len(lengths) else 0,
        "long_ring_fraction": float(np.mean(lengths >= 20)) if len(lengths) else 0,
        "ring_turn_deg_p95": float(np.quantile(turning, .95)) if len(turning) else 0,
        "ring_turn_deg_median": float(np.median(turning)) if len(turning) else 0,
    }


def distances(vertices, faces, source_tree, witnesses, quads):
    samples = np.einsum("sj,fjk->fsk", SAMPLE_WEIGHTS, quads).reshape(-1, 3)
    forward = [float(source_tree.find_nearest(Vector(point))[3]) for point in samples]
    backward = _reverse_error(witnesses, quads, _output_trees(quads))
    return {"mesh_to_source_max": max(forward), "mesh_to_source_p95": float(np.quantile(forward, .95)),
            "source_to_mesh_max": backward}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=int, nargs="+", default=[1200, 2400, 4800, 9600])
    parser.add_argument("--adaptive", type=Path, nargs="*", default=[])
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    args.output.mkdir(parents=True, exist_ok=True)
    archive = np.load(args.cache)
    source_vertices, source_faces, preparation = finish(
        stitch_render_seams(archive["vertices"], archive["faces"]))
    extent = float(np.linalg.norm(np.ptp(source_vertices, axis=0)))
    tree = BVHTree.FromPolygons([Vector(v) for v in source_vertices], source_faces.tolist(), all_triangles=True)
    source_edges = output_edges(source_faces)[0]
    witnesses = np.vstack((source_vertices, source_vertices[source_faces].mean(axis=1),
                           source_vertices[source_edges].mean(axis=1)))
    from remi.features.retopology.instant_meshes._native import Session
    report = {"preparation": preparation, "extent": extent, "meshes": []}
    for target in args.target:
        session = Session(source_vertices.astype(np.float32), source_faces, target_faces=target,
                          pure_quad=True, crease_angle=35, extrinsic=False, align_boundaries=True,
                          deterministic=True, smooth_iterations=2)
        deadline = time.monotonic()+120
        session.start_orientation()
        while session.active:
            assert time.monotonic() < deadline
            time.sleep(.002)
        session.start_position()
        while session.active:
            assert time.monotonic() < deadline
            time.sleep(.002)
        vertices, faces, _normals = session.extract()
        faces = np.asarray(faces, np.int32)
        if faces.shape[1] == 4:
            faces = faces[faces[:, 2] != faces[:, 3]]
        item = {"label": f"native-{target}", "topology": topology(vertices, faces),
                "error": distances(np.asarray(vertices, np.float64), faces, tree, witnesses, np.asarray(vertices)[faces])}
        print("REMI_TOPOLOGY", item["label"], json.dumps(item["topology"]), json.dumps(item["error"]), flush=True)
        report["meshes"].append(item)
        np.savez(args.output / f"native-{target}.npz", vertices=np.asarray(vertices), faces=faces)
        session.stop()
    for path in args.adaptive:
        data = np.load(path)
        vertices, faces = np.asarray(data["vertices"], np.float64), np.asarray(data["faces"], np.int32)
        item = {"label": path.stem, "topology": topology(vertices, faces),
                "error": distances(vertices, faces, tree, witnesses, vertices[faces])}
        print("REMI_TOPOLOGY", item["label"], json.dumps(item["topology"]), json.dumps(item["error"]), flush=True)
        report["meshes"].append(item)
        np.savez(args.output / f"{path.stem}.npz", vertices=vertices, faces=faces)
    (args.output / "topology.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print("REMI_TOPOLOGY_COMPARE_OK", args.output / "topology.json", flush=True)


if __name__ == "__main__":
    main()
