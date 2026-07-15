"""Context-safe Blender orchestration for the Remi UV pipeline."""

from dataclasses import dataclass, field
import math

import bpy

from .analysis import (
    add_distortion_split_seams,
    add_overlap_split_seams,
    analyze_mesh,
    generate_seams,
)
from .metrics import UVStats, evaluate_uv, find_uv_overlaps
from .packing import apply_packing_attempt, pack_candidates, unwrap_candidates
from .settings import get_profile


@dataclass
class UVResult:
    success: bool
    created: bool = False
    profile: str = ""
    classification: str = ""
    solver: str = ""
    chart_count: int = 0
    stats: UVStats | None = None
    warnings: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class _UVCandidate:
    name: str
    uvs: list[tuple[float, float]]
    seams: set[int]
    stats: UVStats
    packer: str


class _ContextSnapshot:
    """Preserve selection, mode, and mesh component selection around UV ops."""

    def __init__(self, context, obj):
        self.context = context
        self.obj = obj
        self.mode = context.mode
        self.active = context.view_layer.objects.active
        self.selected = list(context.selected_objects)
        self.hidden = obj.hide_get()
        self.mesh_select_mode = tuple(context.tool_settings.mesh_select_mode)
        if self.mode == "EDIT_MESH" and self.active:
            self.active.update_from_editmode()
        mesh = obj.data
        self.vertex_selection = [vertex.select for vertex in mesh.vertices]
        self.edge_selection = [edge.select for edge in mesh.edges]
        self.face_selection = [polygon.select for polygon in mesh.polygons]

    def prepare(self):
        if self.context.mode == "EDIT_MESH":
            bpy.ops.object.mode_set(mode="OBJECT")
        elif self.context.mode != "OBJECT":
            raise RuntimeError("Remi UV can only run from Object or Mesh Edit mode")
        bpy.ops.object.select_all(action="DESELECT")
        self.obj.hide_set(False)
        self.obj.select_set(True)
        self.context.view_layer.objects.active = self.obj

    def restore(self):
        if self.context.mode == "EDIT_MESH":
            bpy.ops.object.mode_set(mode="OBJECT")
        mesh = self.obj.data
        for index, selected in enumerate(self.vertex_selection):
            if index < len(mesh.vertices):
                mesh.vertices[index].select = selected
        for index, selected in enumerate(self.edge_selection):
            if index < len(mesh.edges):
                mesh.edges[index].select = selected
        for index, selected in enumerate(self.face_selection):
            if index < len(mesh.polygons):
                mesh.polygons[index].select = selected

        bpy.ops.object.select_all(action="DESELECT")
        for selected_object in self.selected:
            if bpy.data.objects.get(selected_object.name) is not None:
                selected_object.select_set(True)
        if self.active and bpy.data.objects.get(self.active.name) is not None:
            self.context.view_layer.objects.active = self.active
        self.context.tool_settings.mesh_select_mode = self.mesh_select_mode
        if self.mode == "EDIT_MESH" and self.context.view_layer.objects.active:
            try:
                bpy.ops.object.mode_set(mode="EDIT")
            except RuntimeError:
                pass
        self.obj.hide_set(self.hidden)


def _select_all_for_uv(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    if bpy.context.mode != "EDIT_MESH":
        bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")


def _object_mode():
    if bpy.context.mode == "EDIT_MESH":
        bpy.ops.object.mode_set(mode="OBJECT")


def _run_unwrap(obj, method: str, iterations: int):
    _select_all_for_uv(obj)
    result = bpy.ops.uv.unwrap(
        method=method,
        fill_holes=True,
        correct_aspect=True,
        use_subsurf_data=False,
        margin_method="FRACTION",
        margin=0.0,
        no_flip=True,
        iterations=iterations,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender {method} unwrap did not finish")
    _object_mode()
    obj.data.update()


def _pack(
    obj,
    analysis,
    seams: set[int],
    profile,
    texture_size: int,
    margin_px: int,
    conservative: bool = False,
) -> str:
    _select_all_for_uv(obj)
    # Equal surface area should receive equal texture resolution before packing.
    bpy.ops.uv.average_islands_scale(shear=False, scale_uv=False)
    _object_mode()
    obj.data.update()

    try:
        attempts = pack_candidates(
            obj.data,
            analysis,
            seams,
            texture_size,
            margin_px,
            conservative=conservative,
        )
    except RuntimeError as error:
        attempts = []
        print(f"Remi UV: xatlas packing failed, using Blender fallback: {error}")
    if attempts:
        best = attempts[0]
        apply_packing_attempt(obj.data, best)
        print(
            f"Remi UV: {best.name} selected from {len(attempts)} layouts, "
            f"{best.occupancy:.1%} geometry occupancy, "
            f"{best.xatlas_utilization:.1%} padded utilization, "
            f"{best.duration_ms:.0f} ms"
        )
        return best.name

    _select_all_for_uv(obj)
    margin = max(0.0, float(margin_px) / max(1, texture_size))
    result = bpy.ops.uv.pack_islands(
        rotate=True,
        rotate_method="CARDINAL" if conservative else profile.rotate_method,
        scale=True,
        merge_overlap=False,
        margin_method="FRACTION",
        margin=margin * (1.25 if conservative else 1.0),
        pin=False,
        shape_method="AABB" if conservative else "CONCAVE",
    )
    if "FINISHED" not in result:
        raise RuntimeError("Blender UV packing did not finish")
    _object_mode()
    obj.data.update()
    return "BLENDER_PACK"


def _smart_fallback(obj, texture_size: int, margin_px: int):
    _select_all_for_uv(obj)
    result = bpy.ops.uv.smart_project(
        angle_limit=math.radians(66.0),
        margin_method="FRACTION",
        rotate_method="AXIS_ALIGNED",
        island_margin=max(0.0, float(margin_px) / max(1, texture_size)),
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=True,
    )
    if "FINISHED" not in result:
        raise RuntimeError("Blender Smart Project fallback did not finish")
    _object_mode()
    obj.data.update()


def _apply_seams(mesh, seams: set[int], preserve_existing: bool):
    for edge in mesh.edges:
        edge.use_seam = edge.index in seams or (preserve_existing and edge.use_seam)
    mesh.update()


def _store_summary(
    obj,
    profile: str,
    classification: str,
    solver: str,
    stats: UVStats,
    generated_seams: set[int] | None = None,
):
    """Keep the last quality report on the object for UI and downstream tools."""
    obj["remi_uv_profile"] = profile
    obj["remi_uv_classification"] = classification
    obj["remi_uv_solver"] = solver
    obj["remi_uv_chart_count"] = stats.chart_count
    obj["remi_uv_stretch_p95"] = stats.conformal_p95
    obj["remi_uv_occupancy"] = stats.packing_occupancy
    obj["remi_uv_overlap_pairs"] = stats.overlap_pairs
    if generated_seams is not None:
        obj["remi_uv_generated_seams"] = sorted(generated_seams)


def _seams_from_active_uv(mesh) -> set[int]:
    """Recover chart boundaries after an emergency projection fallback."""
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return set()
    edge_faces: dict[int, list[dict[int, tuple[float, float]]]] = {}
    for polygon in mesh.polygons:
        per_edge = {}
        for loop_index in polygon.loop_indices:
            loop = mesh.loops[loop_index]
            per_edge.setdefault(loop.edge_index, {})[loop.vertex_index] = tuple(
                uv_layer.data[loop_index].uv
            )
        # The second endpoint of an edge is stored on the following face loop.
        loop_indices = tuple(polygon.loop_indices)
        for offset, loop_index in enumerate(loop_indices):
            loop = mesh.loops[loop_index]
            next_loop = mesh.loops[loop_indices[(offset + 1) % len(loop_indices)]]
            per_edge[loop.edge_index][next_loop.vertex_index] = tuple(
                uv_layer.data[loop_indices[(offset + 1) % len(loop_indices)]].uv
            )
        for edge_index, endpoints in per_edge.items():
            edge_faces.setdefault(edge_index, []).append(endpoints)

    seams = set()
    for edge_index, face_maps in edge_faces.items():
        if len(face_maps) != 2:
            continue
        endpoints = mesh.edges[edge_index].vertices
        for vertex_index in endpoints:
            uv_a = face_maps[0].get(vertex_index)
            uv_b = face_maps[1].get(vertex_index)
            if uv_a is None or uv_b is None:
                seams.add(edge_index)
                break
            if abs(uv_a[0] - uv_b[0]) > 1.0e-7 or abs(uv_a[1] - uv_b[1]) > 1.0e-7:
                seams.add(edge_index)
                break
    return seams


def _smart_candidate_seams(mesh, analysis, profile, locked_seams: set[int]) -> set[int]:
    seams = _seams_from_active_uv(mesh).union(locked_seams)
    if profile.preserve_material_boundaries:
        seams.update(
            feature.index
            for feature in analysis.edges
            if feature.material_boundary
        )
    return seams


def _capture_uvs(mesh) -> list[tuple[float, float]]:
    uv_layer = mesh.uv_layers.active
    return [tuple(loop.uv) for loop in uv_layer.data]


def _restore_uvs(mesh, coordinates: list[tuple[float, float]]):
    uv_layer = mesh.uv_layers.active
    for loop, uv in zip(uv_layer.data, coordinates):
        loop.uv = uv
    mesh.update()


def _capture_candidate(mesh, name: str, seams: set[int], stats: UVStats, packer: str):
    return _UVCandidate(name, _capture_uvs(mesh), set(seams), stats, packer)


def _restore_candidate(mesh, candidate: _UVCandidate):
    _restore_uvs(mesh, candidate.uvs)
    _apply_seams(mesh, candidate.seams, preserve_existing=False)


def _candidate_rank(candidate: _UVCandidate):
    stats = candidate.stats
    minimum_u, minimum_v, maximum_u, maximum_v = stats.uv_bounds
    inside_tile = (
        minimum_u >= -1.0e-5
        and minimum_v >= -1.0e-5
        and maximum_u <= 1.00001
        and maximum_v <= 1.00001
    )
    usable = stats.valid and inside_tile
    fragmentation = stats.chart_count / max(1, stats.triangle_count)
    stretch_cost = min(10.0, max(0.0, stats.conformal_p95 - 1.0))
    # Artist-usable density is multi-objective. Occupancy remains dominant, but
    # a near-tied layout should not win by exploding into hundreds of islands
    # or accepting visibly worse conformal stretch.
    quality_score = (
        stats.packing_occupancy
        - 0.08 * fragmentation
        - 0.02 * stretch_cost
    )
    return (
        int(usable),
        quality_score if usable else -math.inf,
        stats.packing_occupancy if usable else -math.inf,
        -stats.chart_count,
        -stats.conformal_p95,
    )


def _lightmap_local_faces(
    obj,
    mesh,
    analysis,
    face_indices: set[int],
    profile,
    texture_size: int,
    margin_px: int,
    mandatory_seams: set[int] | None = None,
) -> set[int]:
    """Turn only irreducible foldover faces into independent micro-charts."""
    _object_mode()
    for polygon in mesh.polygons:
        polygon.select = polygon.index in face_indices
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    result = bpy.ops.uv.lightmap_pack(
        PREF_CONTEXT="SEL_FACES",
        PREF_PACK_IN_ONE=True,
        PREF_NEW_UVLAYER=False,
        PREF_BOX_DIV=24,
        PREF_MARGIN_DIV=max(0.001, float(margin_px) / max(1, texture_size)),
    )
    if "FINISHED" not in result:
        raise RuntimeError("Blender local Lightmap repair did not finish")
    _object_mode()
    repaired_seams = _seams_from_active_uv(mesh)
    if mandatory_seams:
        repaired_seams.update(mandatory_seams)
    _apply_seams(mesh, repaired_seams, preserve_existing=False)
    # Repack every existing chart together; only the selected repair faces had
    # their parameterization changed by Lightmap Pack.
    _pack(
        obj,
        analysis,
        repaired_seams,
        profile,
        texture_size,
        margin_px,
        conservative=True,
    )
    return repaired_seams


def _repair_uv_flips(
    obj,
    mesh,
    analysis,
    seams: set[int],
    profile,
    texture_size: int,
    margin_px: int,
    stats: UVStats,
    mandatory_seams: set[int],
    warnings: list[str],
) -> tuple[UVStats, set[int], bool]:
    """Isolate a small set of mirrored faces without discarding good charts."""
    repair_faces = set(stats.flipped_faces)
    if not repair_faces:
        return stats, seams, False
    repair_limit = max(32, stats.triangle_count // 200)
    if len(repair_faces) > repair_limit:
        return stats, seams, False

    previous_uvs = _capture_uvs(mesh)
    previous_seams = set(seams)
    try:
        repaired_seams = _lightmap_local_faces(
            obj,
            mesh,
            analysis,
            repair_faces,
            profile,
            texture_size,
            margin_px,
            mandatory_seams=mandatory_seams,
        )
        candidate = evaluate_uv(
            mesh,
            analysis,
            repaired_seams,
            check_overlaps=True,
        )
    except RuntimeError:
        _restore_uvs(mesh, previous_uvs)
        _apply_seams(mesh, previous_seams, preserve_existing=False)
        return stats, seams, False

    repaired = (
        not candidate.non_finite_uvs
        and not candidate.collapsed_triangles
        and not candidate.flipped_triangles
    )
    if not repaired:
        _restore_uvs(mesh, previous_uvs)
        _apply_seams(mesh, previous_seams, preserve_existing=False)
        return stats, seams, False

    warnings.append(
        f"Repaired {stats.flipped_triangles} flipped UV triangle(s) in "
        f"{len(repair_faces)} face-local chart(s)"
    )
    return candidate, repaired_seams, True


def _repair_uv_overlaps(
    obj,
    mesh,
    analysis,
    seams: set[int],
    profile,
    solver: str,
    texture_size: int,
    margin_px: int,
    stats: UVStats,
    warnings: list[str],
) -> tuple[UVStats, set[int]]:
    """Repair local foldovers while retaining the best valid attempt."""
    for _repair_pass in range(max(2, profile.repair_passes)):
        if not stats.overlap_pairs:
            break
        overlap_details = find_uv_overlaps(mesh)
        overlap_pairs = [
            (detail["polygon_a"], detail["polygon_b"])
            for detail in overlap_details
        ]
        additions, repair_faces = add_overlap_split_seams(
            analysis,
            overlap_pairs,
            seams,
        )
        if not additions:
            break

        previous_uvs = _capture_uvs(mesh)
        previous_seams = set(seams)
        candidate_seams = seams.union(additions)
        _apply_seams(mesh, candidate_seams, preserve_existing=False)
        _run_unwrap(obj, solver, profile.iterations)
        _pack(obj, analysis, candidate_seams, profile, texture_size, margin_px)
        candidate = evaluate_uv(mesh, analysis, candidate_seams, check_overlaps=True)
        print(
            f"Remi UV: overlap repair tested {len(repair_faces)} local face "
            f"regions and left {candidate.overlap_pairs} intersections"
        )
        improved = (
            candidate.overlap_pairs < stats.overlap_pairs
            and not candidate.collapsed_triangles
            and not candidate.flipped_triangles
        )
        if not improved:
            _restore_uvs(mesh, previous_uvs)
            _apply_seams(mesh, previous_seams, preserve_existing=False)
            # Seam-only parameterization can still weld vertex-only face fans.
            # Re-project just the minimal conflicting face cover as independent
            # micro-charts, leaving every other island untouched.
            local_seams = _lightmap_local_faces(
                obj,
                mesh,
                analysis,
                repair_faces,
                profile,
                texture_size,
                margin_px,
            )
            local_candidate = evaluate_uv(
                mesh,
                analysis,
                local_seams,
                check_overlaps=True,
            )
            local_improved = (
                local_candidate.overlap_pairs < stats.overlap_pairs
                and not local_candidate.collapsed_triangles
                and not local_candidate.flipped_triangles
            )
            if not local_improved:
                _restore_uvs(mesh, previous_uvs)
                _apply_seams(mesh, previous_seams, preserve_existing=False)
                break
            warnings.append(
                f"Resolved {stats.overlap_pairs - local_candidate.overlap_pairs} "
                f"UV intersections with {len(repair_faces)} face-local charts"
            )
            stats = local_candidate
            seams = local_seams
            continue

        warnings.append(
            f"Repaired {stats.overlap_pairs - candidate.overlap_pairs} UV "
            f"intersections around {len(repair_faces)} local face regions"
        )
        stats = candidate
        seams = candidate_seams
    return stats, seams


def ensure_remi_uv(
    obj: bpy.types.Object,
    profile_id: str = "NORMAL_BAKE",
    texture_size: int = 2048,
    margin_px: int = 4,
    preserve_existing_seams: bool = True,
    replace_existing: bool = False,
) -> UVResult:
    """Create validated, packed UVs on ``obj`` using the Remi UV pipeline."""
    if obj is None or obj.type != "MESH":
        return UVResult(False, error="Remi UV needs a mesh object")
    if not obj.data.polygons:
        return UVResult(False, error=f"'{obj.name}' has no faces")
    profile = get_profile(profile_id)
    initial_warnings = []
    if obj.data.uv_layers and not replace_existing:
        try:
            existing_analysis = analyze_mesh(obj.data)
            existing_seams = _seams_from_active_uv(obj.data)
            existing_stats = evaluate_uv(
                obj.data,
                existing_analysis,
                existing_seams,
                check_overlaps=True,
            )
            if existing_stats.valid:
                _store_summary(
                    obj,
                    profile.identifier,
                    existing_analysis.classification,
                    "EXISTING",
                    existing_stats,
                )
                return UVResult(
                    True,
                    created=False,
                    profile=profile.identifier,
                    classification=existing_analysis.classification,
                    solver="EXISTING",
                    chart_count=existing_stats.chart_count,
                    stats=existing_stats,
                    warnings=["Kept the existing validated UV map"],
                )
            initial_warnings.append(
                "Existing UV map failed validation and was regenerated"
            )
        except ValueError:
            initial_warnings.append(
                "Existing UV map could not be validated and was regenerated"
            )

    snapshot = _ContextSnapshot(bpy.context, obj)
    warnings = list(initial_warnings)
    fallback_used = False
    smart_baseline_selected = False
    xatlas_chart_selected = False
    solver_used = ""

    try:
        snapshot.prepare()
        mesh = obj.data
        analysis = analyze_mesh(mesh)
        existing_marked_seams = {edge.index for edge in mesh.edges if edge.use_seam}
        previous_generated_seams = set(obj.get("remi_uv_generated_seams", ()))
        locked_artist_seams = (
            existing_marked_seams.difference(previous_generated_seams)
            if preserve_existing_seams
            else set()
        )
        # A regenerated map starts from current artist constraints, not seams
        # emitted by the previous automatic run. This keeps repeated runs
        # deterministic while retaining genuinely new manual marks.
        for edge in mesh.edges:
            edge.use_seam = edge.index in locked_artist_seams
        mesh.update()
        if mesh.uv_layers.active is None:
            uv_layer = mesh.uv_layers.new(name="RemiUV")
        else:
            uv_layer = mesh.uv_layers.active
            if replace_existing and uv_layer.name != "RemiUV":
                uv_layer.name = "RemiUV"
        mesh.uv_layers.active = uv_layer
        uv_layer.active_render = True

        seams = generate_seams(
            mesh,
            analysis,
            profile,
            preserve_existing=preserve_existing_seams,
        )
        _apply_seams(mesh, seams, preserve_existing_seams)
        seams = {edge.index for edge in mesh.edges if edge.use_seam}

        # Solvers are routed as a failure-tolerant chain. Minimum Stretch is
        # the normal path; Angle Based and LSCM remain robust initializers.
        solvers = [profile.solver, "ANGLE_BASED", "CONFORMAL"]
        solvers = list(dict.fromkeys(solvers))
        unwrap_stats = None
        last_error = None
        best_invalid = None
        parameterization_repaired = False
        for solver in solvers:
            try:
                _run_unwrap(obj, solver, profile.iterations)
                candidate = evaluate_uv(mesh, analysis, seams, check_overlaps=False)
                if not candidate.collapsed_triangles and not candidate.flipped_triangles:
                    solver_used = solver
                    unwrap_stats = candidate
                    break
                invalid_rank = (
                    candidate.non_finite_uvs,
                    candidate.collapsed_triangles,
                    candidate.flipped_triangles,
                    candidate.conformal_p95,
                )
                if best_invalid is None or invalid_rank < best_invalid[0]:
                    best_invalid = (
                        invalid_rank,
                        solver,
                        candidate,
                        _capture_uvs(mesh),
                    )
                last_error = RuntimeError(
                    f"{solver} produced {candidate.collapsed_triangles} collapsed and "
                    f"{candidate.flipped_triangles} flipped triangles"
                )
            except RuntimeError as error:
                last_error = error

        if unwrap_stats is None and best_invalid is not None:
            _rank, solver_used, unwrap_stats, coordinates = best_invalid
            _restore_uvs(mesh, coordinates)
            if (
                not unwrap_stats.non_finite_uvs
                and not unwrap_stats.collapsed_triangles
                and unwrap_stats.flipped_triangles
            ):
                unwrap_stats, seams, parameterization_repaired = _repair_uv_flips(
                    obj,
                    mesh,
                    analysis,
                    seams,
                    profile,
                    texture_size,
                    margin_px,
                    unwrap_stats,
                    locked_artist_seams,
                    warnings,
                )

        # If Blender's three parameterizers all fail on topology emitted by a
        # remesher, xatlas remains an independent charting/LSCM recovery path.
        parameterization_usable = (
            unwrap_stats is not None
            and not unwrap_stats.non_finite_uvs
            and not unwrap_stats.collapsed_triangles
            and not unwrap_stats.flipped_triangles
        )
        if not parameterization_usable and not locked_artist_seams:
            generated_best = None
            for attempt in unwrap_candidates(
                mesh,
                analysis,
                texture_size,
                margin_px,
            ):
                apply_packing_attempt(mesh, attempt)
                generated_seams = _seams_from_active_uv(mesh)
                _apply_seams(mesh, generated_seams, preserve_existing=False)
                generated_stats = evaluate_uv(
                    mesh,
                    analysis,
                    generated_seams,
                    check_overlaps=True,
                )
                generated_candidate = _capture_candidate(
                    mesh,
                    attempt.name,
                    generated_seams,
                    generated_stats,
                    attempt.name,
                )
                if (
                    generated_stats.valid
                    and (
                        generated_best is None
                        or _candidate_rank(generated_candidate)
                        > _candidate_rank(generated_best)
                    )
                ):
                    generated_best = generated_candidate
            if generated_best is not None:
                _restore_candidate(mesh, generated_best)
                seams = generated_best.seams
                unwrap_stats = generated_best.stats
                solver_used = "XATLAS"
                xatlas_chart_selected = True
                parameterization_repaired = True
                warnings.append(
                    "Blender parameterizers were invalid; recovered with "
                    f"{generated_best.name}"
                )

        if unwrap_stats is None:
            raise last_error or RuntimeError("All Remi UV parameterization methods failed")
        if (
            unwrap_stats.non_finite_uvs
            or unwrap_stats.collapsed_triangles
            or unwrap_stats.flipped_triangles
        ):
            raise last_error or RuntimeError("All Remi UV parameterization methods failed")

        for _repair_pass in range(0 if parameterization_repaired else profile.repair_passes):
            if unwrap_stats.conformal_p95 <= profile.stretch_limit:
                break
            bad_faces = {
                face
                for face, distortion in unwrap_stats.face_distortion.items()
                if distortion > profile.stretch_limit
            }
            additions = add_distortion_split_seams(analysis, bad_faces, seams)
            if not additions:
                warnings.append(
                    f"95th-percentile stretch {unwrap_stats.conformal_p95:.2f} "
                    f"exceeds the {profile.stretch_limit:.2f} profile target"
                )
                break
            seams.update(additions)
            _apply_seams(mesh, seams, preserve_existing=True)
            _run_unwrap(obj, solver_used, profile.iterations)
            unwrap_stats = evaluate_uv(mesh, analysis, seams, check_overlaps=False)

        packer_used = _pack(
            obj,
            analysis,
            seams,
            profile,
            texture_size,
            margin_px,
        )
        final_stats = evaluate_uv(mesh, analysis, seams, check_overlaps=True)
        if final_stats.overlap_pairs:
            packer_used = _pack(
                obj,
                analysis,
                seams,
                profile,
                texture_size,
                margin_px,
                conservative=True,
            )
            final_stats = evaluate_uv(mesh, analysis, seams, check_overlaps=True)

        # Very large foldover sets indicate that the proposed geometry-aware
        # chart layout was a poor fit. Route those through Smart Project first,
        # then retain its charts as seams for precise local repair.
        overlap_fallback_threshold = max(8, final_stats.triangle_count // 1000)
        if final_stats.overlap_pairs > overlap_fallback_threshold:
            _apply_seams(mesh, locked_artist_seams, preserve_existing=False)
            _smart_fallback(obj, texture_size, margin_px)
            fallback_used = True
            seams = _smart_candidate_seams(
                mesh,
                analysis,
                profile,
                locked_artist_seams,
            )
            _apply_seams(mesh, seams, preserve_existing=False)
            packer_used = _pack(
                obj,
                analysis,
                seams,
                profile,
                texture_size,
                margin_px,
            )
            final_stats = evaluate_uv(mesh, analysis, seams, check_overlaps=True)

        # Packing cannot repair intersections between triangles in the same
        # chart. Isolate a minimal cover of conflicting local face fans and
        # retain the attempt only when it reduces the intersection count.
        final_stats, seams = _repair_uv_overlaps(
            obj,
            mesh,
            analysis,
            seams,
            profile,
            solver_used,
            texture_size,
            margin_px,
            final_stats,
            warnings,
        )

        # Benchmark every valid Remi chart layout against Blender Smart
        # Project. Smart's own packed result and an xatlas repack of those same
        # charts are both eligible, so the add-on cannot return a measurably
        # looser valid layout merely because Remi generated it first.
        if final_stats.valid and not fallback_used and not locked_artist_seams:
            remi_candidate = _capture_candidate(
                mesh,
                "REMI",
                seams,
                final_stats,
                packer_used,
            )
            smart_warnings = []
            try:
                _apply_seams(mesh, locked_artist_seams, preserve_existing=False)
                _smart_fallback(obj, texture_size, margin_px)
                smart_seams = _smart_candidate_seams(
                    mesh,
                    analysis,
                    profile,
                    locked_artist_seams,
                )
                _apply_seams(mesh, smart_seams, preserve_existing=False)
                smart_raw_stats = evaluate_uv(
                    mesh,
                    analysis,
                    smart_seams,
                    check_overlaps=True,
                )
                smart_candidates = [
                    _capture_candidate(
                        mesh,
                        "SMART_NATIVE_PACK",
                        smart_seams,
                        smart_raw_stats,
                        "BLENDER_SMART_PACK",
                    )
                ]

                smart_packer = _pack(
                    obj,
                    analysis,
                    smart_seams,
                    profile,
                    texture_size,
                    margin_px,
                )
                smart_packed_stats = evaluate_uv(
                    mesh,
                    analysis,
                    smart_seams,
                    check_overlaps=True,
                )
                smart_packed_stats, smart_seams = _repair_uv_overlaps(
                    obj,
                    mesh,
                    analysis,
                    smart_seams,
                    profile,
                    solver_used,
                    texture_size,
                    margin_px,
                    smart_packed_stats,
                    smart_warnings,
                )
                smart_candidates.append(_capture_candidate(
                    mesh,
                    "SMART_XATLAS_PACK",
                    smart_seams,
                    smart_packed_stats,
                    smart_packer,
                ))
                smart_candidate = max(smart_candidates, key=_candidate_rank)

                if _candidate_rank(smart_candidate) > _candidate_rank(remi_candidate):
                    _restore_candidate(mesh, smart_candidate)
                    seams = smart_candidate.seams
                    final_stats = smart_candidate.stats
                    packer_used = smart_candidate.packer
                    smart_baseline_selected = True
                    warnings.extend(smart_warnings)
                    warnings.append(
                        "Smart charting won the quality benchmark: "
                        f"{smart_candidate.stats.packing_occupancy:.1%} occupancy "
                        f"vs {remi_candidate.stats.packing_occupancy:.1%}"
                    )
                else:
                    _restore_candidate(mesh, remi_candidate)
                    seams = remi_candidate.seams
                    final_stats = remi_candidate.stats
                    packer_used = remi_candidate.packer
                    warnings.append(
                        "Remi charting retained after beating the Smart UV "
                        f"baseline ({final_stats.packing_occupancy:.1%} vs "
                        f"{smart_candidate.stats.packing_occupancy:.1%} occupancy)"
                    )
            except RuntimeError as error:
                _restore_candidate(mesh, remi_candidate)
                seams = remi_candidate.seams
                final_stats = remi_candidate.stats
                packer_used = remi_candidate.packer
                warnings.append(f"Smart UV benchmark could not run: {error}")

        if not final_stats.valid:
            # A final emergency route keeps malformed production inputs usable,
            # while reporting that semantic Remi charting could not be retained.
            if not fallback_used:
                _apply_seams(mesh, locked_artist_seams, preserve_existing=False)
                _smart_fallback(obj, texture_size, margin_px)
                fallback_used = True
                seams = _smart_candidate_seams(
                    mesh,
                    analysis,
                    profile,
                    locked_artist_seams,
                )
                _apply_seams(mesh, seams, preserve_existing=False)
                packer_used = _pack(
                    obj,
                    analysis,
                    seams,
                    profile,
                    texture_size,
                    margin_px,
                )
                final_stats = evaluate_uv(mesh, analysis, seams, check_overlaps=True)
                final_stats, seams = _repair_uv_overlaps(
                    obj,
                    mesh,
                    analysis,
                    seams,
                    profile,
                    solver_used,
                    texture_size,
                    margin_px,
                    final_stats,
                    warnings,
                )

        if final_stats.valid and not locked_artist_seams:
            incumbent = _capture_candidate(
                mesh,
                "CURRENT",
                seams,
                final_stats,
                packer_used,
            )
            best_candidate = incumbent
            try:
                generated_attempts = unwrap_candidates(
                    mesh,
                    analysis,
                    texture_size,
                    margin_px,
                )
                for attempt in generated_attempts:
                    apply_packing_attempt(mesh, attempt)
                    generated_seams = _seams_from_active_uv(mesh)
                    _apply_seams(mesh, generated_seams, preserve_existing=False)
                    generated_stats = evaluate_uv(
                        mesh,
                        analysis,
                        generated_seams,
                        check_overlaps=True,
                    )
                    candidate = _capture_candidate(
                        mesh,
                        attempt.name,
                        generated_seams,
                        generated_stats,
                        attempt.name,
                    )
                    if _candidate_rank(candidate) > _candidate_rank(best_candidate):
                        best_candidate = candidate

                _restore_candidate(mesh, best_candidate)
                seams = best_candidate.seams
                final_stats = best_candidate.stats
                packer_used = best_candidate.packer
                if best_candidate is not incumbent:
                    xatlas_chart_selected = True
                    smart_baseline_selected = False
                    warnings.append(
                        "xatlas chart generation won the quality benchmark: "
                        f"{final_stats.chart_count} charts at "
                        f"{final_stats.packing_occupancy:.1%} occupancy"
                    )
            except RuntimeError as error:
                _restore_candidate(mesh, incumbent)
                seams = incumbent.seams
                final_stats = incumbent.stats
                packer_used = incumbent.packer
                warnings.append(f"xatlas chart benchmark could not run: {error}")

        if not final_stats.valid:
            return UVResult(
                False,
                created=True,
                profile=profile.identifier,
                classification=analysis.classification,
                solver="SMART_FALLBACK" if fallback_used else solver_used,
                chart_count=final_stats.chart_count,
                stats=final_stats,
                warnings=warnings,
                error=(
                    "UV validation failed: "
                    f"{final_stats.collapsed_triangles} collapsed, "
                    f"{final_stats.flipped_triangles} flipped, "
                    f"{final_stats.overlap_pairs} overlaps"
                ),
            )

        if fallback_used:
            warnings.append("Used Smart Project only after Remi UV validation failed")
        if final_stats.conformal_p95 > profile.stretch_limit:
            warnings.append(
                f"Final 95th-percentile stretch is {final_stats.conformal_p95:.2f}"
            )
        print(
            f"Remi UV: '{obj.name}' {analysis.classification.lower()}, "
            f"{final_stats.chart_count} charts, p95 stretch "
            f"{final_stats.conformal_p95:.2f}, {final_stats.packing_occupancy:.1%} occupancy"
        )
        if xatlas_chart_selected:
            chart_solver = "XATLAS_CHARTS"
        elif smart_baseline_selected:
            chart_solver = "SMART_BENCHMARK"
        elif fallback_used:
            chart_solver = "SMART_FALLBACK"
        else:
            chart_solver = solver_used
        final_solver = f"{chart_solver}+{packer_used}"
        _store_summary(
            obj,
            profile.identifier,
            analysis.classification,
            final_solver,
            final_stats,
            seams,
        )
        return UVResult(
            True,
            created=True,
            profile=profile.identifier,
            classification=analysis.classification,
            solver=final_solver,
            chart_count=final_stats.chart_count,
            stats=final_stats,
            warnings=warnings,
        )
    except (RuntimeError, ValueError) as error:
        return UVResult(
            False,
            created=bool(obj.data.uv_layers),
            profile=profile.identifier,
            error=str(error),
            warnings=warnings,
        )
    finally:
        snapshot.restore()
