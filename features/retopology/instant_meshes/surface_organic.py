"""Field-aligned adaptive density on the native quad layout.

The previous organic prototype built quads from a decimated triangle dual. That
invents edge flow and produced ~46% regular vertices, 5,000+ poles and 58 degree
skew on the real asset. This module instead solves the field-aligned layout at
the smallest density that meets the source-error budget, so every edge follows
the same cross-field as ordinary Instant Meshes and loops stay continuous.

This is the standard field-aligned retopology route (Jakob et al. 2015), not a
triangle-to-quad guess and not recovery of the original GLB quads. The solver's
target edge length is global, so density is uniform per solve; the search below
makes the *global* density as low as the detail budget allows.
"""

import math

import numpy as np
from mathutils import Vector

from .adaptive import SAMPLE_WEIGHTS
from .quality import mesh_quality, output_edges
from .surface_adaptive import _output_trees, _reverse_error, _single_vertex_fans
from .surface_topology import consistent_winding


# The solver's placement error falls roughly as density**-0.55 on real meshes.
# This exponent is a search heuristic only; acceptance always re-measures.
DENSITY_EXPONENT = .55
MAX_DENSITY_ATTEMPTS = 6


def _solve(session, tick):
    """Cooperative wait for the native solver; closing this generator stops it."""
    try:
        session.start_orientation()
        while session.active:
            tick()
            yield .02
        session.start_position()
        while session.active:
            tick()
            yield .02
        return session.extract()
    finally:
        session.stop()


def _nearest(points, tree):
    points = np.asarray(points)
    values = np.empty(len(points))
    for index, point in enumerate(points):
        values[index] = tree.find_nearest(Vector(point))[3]
    return values


def _topology_ok(quality, source_quality, source_boundary, faces):
    ideal = 1-quality["irregular_vertices"]/max(quality["vertices"], 1)
    # Open sources keep an open layout, but the solver may sample the same
    # boundary with a different edge count. Closed sources must stay closed.
    boundary_ok = quality["boundary_edges"] == 0 if source_boundary == 0 else quality["boundary_edges"] > 0
    return bool(
        quality["quad_fraction"] == 1 and
        quality["components"] == source_quality["components"] and
        quality["euler_characteristic"] == source_quality["euler_characteristic"] and
        boundary_ok and
        quality["nonmanifold_edges"] == 0 and
        quality["degenerate_faces"] == 0 and
        ideal >= .90 and quality["aspect_p95"] <= 2.5 and
        quality["angle_error_p95_deg"] <= 35 and consistent_winding(faces))


def _summary(attempts):
    return "; ".join(
        f"{item['faces']:,} quads" +
        (f" @ {item['sampled_error_max']:.4g}" if "sampled_error_max" in item else " (topology rejected)")
        for item in attempts[-3:])


def rebuild_field_aligned(vertices, triangles, *, error, max_output, tick, status,
                          crease_angle=35.0, guides=(), options=None):
    """Smallest field-aligned density meeting the source-error budget.

    vertices/triangles are the seam-prepared analysis source. The native solver
    supplies both the cross-field and the quad layout; no triangle-dual quads.
    """
    from mathutils.bvhtree import BVHTree
    from ._native import Session
    from .guides import send_guides

    vertices = np.asarray(vertices, np.float64)
    triangles = np.asarray(triangles, np.int32)
    options = dict(options or {})
    source_quality = mesh_quality(vertices, triangles)
    source_boundary = source_quality["boundary_edges"]
    source_edges = output_edges(triangles)[0]
    witnesses = np.vstack((vertices, vertices[triangles].mean(axis=1),
                           vertices[source_edges].mean(axis=1)))
    search_witnesses = witnesses[::max(1, len(witnesses)//12000)]
    tree = BVHTree.FromPolygons([Vector(v) for v in vertices], triangles.tolist(), all_triangles=True)
    target = min(max(512, len(triangles)//64), 4096)
    attempts = []
    tried_targets = set()
    last_actual = 0
    reason = "No field-aligned solution was attempted"
    for attempt in range(MAX_DENSITY_ATTEMPTS):
        tick()
        status(f"Solving field-aligned layout · {target:,} requested quads", .08+.14*attempt)
        session = Session(vertices.astype(np.float32), triangles, target_faces=int(target),
                          pure_quad=True, crease_angle=float(crease_angle),
                          extrinsic=bool(options.get("extrinsic", False)),
                          align_boundaries=bool(options.get("align_boundaries", True)),
                          deterministic=bool(options.get("deterministic", True)),
                          smooth_iterations=int(options.get("smooth_iterations", 2)))
        if any(guide.enabled for guide in guides):
            try:
                send_guides(session, [guide for guide in guides if guide.enabled])
            except Exception as guide_error:
                session.stop()
                raise ValueError(f"Field-aligned rebuild could not apply guides: {guide_error}") from guide_error
        output, faces, _normals = yield from _solve(session, tick)
        output = np.asarray(output, np.float64)
        faces = np.asarray(faces, np.int32)
        if faces.shape[1] == 4:
            faces = faces[faces[:, 2] != faces[:, 3]]
        tried_targets.add(int(target))
        quality = mesh_quality(output, faces)
        item = {"target_requested": int(target), "faces": len(faces), "quality": quality}
        attempts.append(item)
        if not _topology_ok(quality, source_quality, source_boundary, faces):
            reason = "Low field density left holes or non-manifold fans"
            status(f"Rejecting discontinuous coarse layout · {len(faces):,} quads", .3)
            next_target = max(512, int(target*1.7), len(faces)*2)
            if next_target > max_output or next_target in tried_targets or len(faces) == last_actual:
                break
            target, last_actual = next_target, len(faces)
            continue
        quads = output[faces]
        cheap = np.vstack((quads.reshape(-1, 3), quads.mean(axis=1)))
        measured = float(max(_nearest(cheap, tree).max(),
                             _reverse_error(search_witnesses, quads, _output_trees(quads))))
        item["sampled_error_max"] = measured
        status(f"Checking field-aligned density · {len(faces):,} quads · error {measured:.4g}", .55)
        if measured <= error:
            status("Validating every source and output sample", .7)
            maximum = float(max(
                _nearest(np.einsum("sj,fjk->fsk", SAMPLE_WEIGHTS, quads).reshape(-1, 3), tree).max(),
                _reverse_error(witnesses, quads, _output_trees(quads))))
            item["validated_error_max"], item["accepted"] = maximum, maximum <= error
            if maximum <= error:
                status("Checking quad vertex connectivity and winding", .9)
                if not (yield from _single_vertex_fans(faces, tick)):
                    reason = "The field-aligned layout contains a non-manifold vertex fan"
                    next_target = max(512, len(faces)*2)
                    if next_target > max_output or next_target in tried_targets:
                        break
                    target, last_actual = next_target, len(faces)
                    continue
                metrics = {
                    "backend": "FIELD_ALIGNED", "attempts": attempts,
                    "source_components": source_quality["components"],
                    "sampled_patch_error_max": maximum,
                    "output_quality": quality, "patches": 0,
                    "poles": quality["interior_poles"],
                    "ideal_valence_fraction": 1-quality["irregular_vertices"]/max(quality["vertices"], 1),
                    "density_search": "ERROR_DRIVEN",
                    "source_triangle_equivalent_reduced": len(faces)*2 <= len(triangles),
                    "selected_faces": len(faces),
                }
                return output.astype(np.float32), faces, metrics
        reason = "The field-aligned density is above the source-error limit"
        ratio = (measured/error)**(1/DENSITY_EXPONENT) if measured > 0 and math.isfinite(measured) else 2.
        next_target = int(math.ceil(max(len(faces), 256)*min(8., max(1.25, ratio))*1.05))
        if next_target <= len(faces):
            next_target = max(len(faces)*2, target*2)
        if next_target > max_output or next_target in tried_targets or len(faces) == last_actual:
            break
        target, last_actual = next_target, len(faces)
    summary = _summary(attempts)
    raise ValueError(f"Field-aligned layout could not meet the {error:.4g} source-error limit "
                     f"within {max_output:,} quads ({reason})" + (f"; attempts: {summary}" if summary else ""))
