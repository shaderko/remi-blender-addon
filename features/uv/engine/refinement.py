"""Bounded, packing-aware search over local UV cuts, merges and seam moves."""

from dataclasses import dataclass

import numpy as np

from .metrics import _chart_ids, evaluate_uv
from .packing import _input_arrays, pack_candidates, apply_packing_attempt


@dataclass
class SeamProposal:
    name: str
    seams: set[int]
    flatten_faces: set[int]


def seam_proposals(mesh, analysis, seams, mandatory):
    face_charts, count = _chart_ids(analysis, seams)
    uvs, triangles, triangle_charts, _ = _input_arrays(mesh, analysis, seams)
    points = uvs[triangles]
    a, b = points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]
    areas = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) * 0.5
    faces_by_chart = [set() for _ in range(count)]
    centroids = np.zeros((len(mesh.polygons), 2))
    for polygon in mesh.polygons:
        faces_by_chart[face_charts[polygon.index]].add(polygon.index)
        centroids[polygon.index] = uvs[list(polygon.loop_indices)].mean(axis=0)

    waste = []
    for chart, faces in enumerate(faces_by_chart):
        if len(faces) < 16:
            continue
        mask = triangle_charts == chart
        cloud = points[mask].reshape(-1, 2)
        low, high = cloud.min(axis=0), cloud.max(axis=0)
        waste.append((float(np.prod(high - low) - areas[mask].sum()), chart, (low + high) * 0.5))
    waste.sort(key=lambda item: (-item[0], item[1]))
    proposals = []
    for _, chart, midpoint in waste[:2]:
        for axis in (0, 1):
            additions = {
                edge.index for edge in analysis.edges
                if len(edge.faces) == 2
                and face_charts[edge.faces[0]] == chart == face_charts[edge.faces[1]]
                and (centroids[edge.faces[0], axis] > midpoint[axis])
                != (centroids[edge.faces[1], axis] > midpoint[axis])
            }
            if additions:
                proposals.append(SeamProposal(f"split {chart}/{axis}", seams | additions, set()))

    interfaces = {}
    for edge in analysis.edges:
        if len(edge.faces) != 2:
            continue
        a, b = sorted(face_charts[face] for face in edge.faces)
        if a != b:
            interfaces.setdefault((a, b), []).append(edge)
    # Favor long removable boundaries between small compatible charts. Sharp,
    # material, non-manifold and artist boundaries are mandatory constraints.
    joins = []
    for (a, b), edges in interfaces.items():
        if any(e.index in mandatory or e.angle > 0.75 for e in edges):
            continue
        faces = faces_by_chart[a] | faces_by_chart[b]
        if len(faces) > 2000:
            continue
        score = sum(e.length for e in edges) / max(1, len(faces))
        joins.append((score, a, b, {e.index for e in edges}, faces))
    joins.sort(key=lambda item: (-item[0], item[1], item[2]))
    for _, a, b, removed, faces in joins[:2]:
        merged = seams - removed
        proposals.append(SeamProposal(f"merge {a}+{b}", merged, faces))
        # Relocate the interface using a cut across the combined region, then
        # flatten only that region. This can improve shape without adding charts.
        cloud = centroids[sorted(faces)]
        axis = int(np.argmax(np.ptp(cloud, axis=0)))
        midpoint = float(np.median(cloud[:, axis]))
        moved = {
            e.index for e in analysis.edges if len(e.faces) == 2
            and all(face in faces for face in e.faces)
            and (centroids[e.faces[0], axis] > midpoint) != (centroids[e.faces[1], axis] > midpoint)
        }
        if moved and moved != removed:
            proposals.append(SeamProposal(f"move seam {a}+{b}", merged | moved, faces))
    return proposals


def refine_atlas(mesh, analysis, seams, profile, texture_size, margin_px, mandatory, unwrap_faces):
    """Retain the best verified atlas across at most two rounds of local edits."""
    best_uvs = np.asarray([tuple(loop.uv) for loop in mesh.uv_layers.active.data])
    best_seams = set(seams)
    best_stats = evaluate_uv(mesh, analysis, seams, True, texture_size, margin_px)
    trace = []
    if not best_stats.valid or best_stats.chart_count <= 1:
        return best_uvs, best_seams, best_stats, trace

    def restore(uvs, cuts):
        for loop, uv in zip(mesh.uv_layers.active.data, uvs):
            loop.uv = uv
        for edge in mesh.edges:
            edge.use_seam = edge.index in cuts
        mesh.update()

    # Freeze quality limits to the initial valid result; repeated small steps
    # cannot gradually accumulate density or distortion damage.
    reference = best_stats
    for _round in range(2):
        restore(best_uvs, best_seams)
        proposals = seam_proposals(mesh, analysis, best_seams, mandatory)
        round_uvs, round_seams = best_uvs.copy(), set(best_seams)
        improved = False
        for proposal in proposals:
            restore(round_uvs, proposal.seams)
            if not mandatory.issubset(proposal.seams):
                continue
            try:
                if proposal.flatten_faces:
                    unwrap_faces(proposal.flatten_faces)
                for attempt in pack_candidates(mesh, analysis, proposal.seams, texture_size, margin_px):
                    apply_packing_attempt(mesh, attempt)
                    stats = evaluate_uv(mesh, analysis, proposal.seams, True, texture_size, margin_px)
                    quality_ok = (
                        stats.valid
                        and stats.conformal_p95 <= reference.conformal_p95 * 1.025
                        and stats.conformal_max <= reference.conformal_max * 1.025
                        and stats.density_p05 >= reference.density_p05 * 0.975
                        and stats.density_p95 <= reference.density_p95 * 1.025
                        and stats.chart_count <= reference.chart_count + 8
                    )
                    if quality_ok and stats.packing_occupancy > best_stats.packing_occupancy + 0.0005:
                        best_uvs, best_seams, best_stats = attempt.uvs.copy(), set(proposal.seams), stats
                        improved = True
                        trace.append(
                            f"{proposal.name}: {stats.chart_count} charts, "
                            f"{stats.packing_occupancy:.1%} coverage"
                        )
            except (RuntimeError, ValueError):
                # Each proposal is speculative. Its failure cannot discard a
                # known valid atlas or prevent testing independent proposals.
                continue
            finally:
                restore(best_uvs, best_seams)
        if not improved:
            break
    restore(best_uvs, best_seams)
    return best_uvs, best_seams, best_stats, trace
