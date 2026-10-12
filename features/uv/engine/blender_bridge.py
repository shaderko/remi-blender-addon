"""Context-safe Blender orchestration for the Remi UV pipeline."""

from dataclasses import dataclass, field
import hashlib
import json
import math

import bpy
import numpy as np

from .analysis import (
    add_distortion_split_seams,
    add_overlap_split_seams,
    analyze_mesh,
    generate_seams,
)
from .metrics import UVStats, evaluate_uv, find_uv_overlaps
from .packing import apply_packing_attempt, pack_candidates, unwrap_candidates
from .settings import get_profile
from .refinement import refine_atlas
from .fitting import fit_islands
from .packing import _input_arrays


_LARGE_MESH_TRIANGLE_THRESHOLD = 20_000


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
    warnings: list[str] = field(default_factory=list)


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
        ranked = []
        for attempt in attempts:
            apply_packing_attempt(obj.data, attempt)
            stats = evaluate_uv(obj.data, analysis, seams, True, texture_size, margin_px)
            ranked.append(((stats.valid, -stats.collapsed_triangles, -stats.flipped_triangles,
                            -stats.overlap_pairs, stats.gap_valid, attempt.occupancy), attempt))
        best = max(ranked, key=lambda item: item[0])[1]
        apply_packing_attempt(obj.data, best)
        print(
            f"Remi UV: {best.name} selected from {len(attempts)} layouts, "
            f"{best.occupancy:.1%} geometry occupancy, "
            f"{best.xatlas_utilization:.1%} initial raster utilization, "
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
        margin=margin,
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
        scale_to_bounds=False,
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
    texture_size: int = 0,
    margin_px: int = 0,
):
    """Keep the last quality report on the object for UI and downstream tools."""
    obj["remi_uv_profile"] = profile
    obj["remi_uv_classification"] = classification
    obj["remi_uv_solver"] = solver
    obj["remi_uv_chart_count"] = stats.chart_count
    obj["remi_uv_stretch_p95"] = stats.conformal_p95
    obj["remi_uv_occupancy"] = stats.packing_occupancy
    obj["remi_uv_overlap_pairs"] = stats.overlap_pairs
    obj["remi_uv_triangle_count"] = stats.triangle_count
    obj["remi_uv_loop_count"] = len(obj.data.loops)
    obj["remi_uv_polygon_count"] = len(obj.data.polygons)
    obj["remi_uv_layer_name"] = obj.data.uv_layers.active.name
    obj["remi_uv_texture_size"] = int(texture_size)
    obj["remi_uv_margin_px"] = int(margin_px)
    obj["remi_uv_bounds"] = list(stats.uv_bounds)
    obj["remi_uv_fingerprint"] = _uv_fingerprint(obj)
    saved_stats = stats.to_dict()
    for key in ("valid", "face_distortion", "flipped_faces"):
        saved_stats.pop(key, None)
    obj["remi_uv_validation"] = json.dumps(saved_stats)
    if generated_seams is not None:
        obj["remi_uv_generated_seams"] = sorted(generated_seams)


def _uv_fingerprint(obj):
    """Content-address validation; counts alone do not detect UV or mesh edits."""
    mesh = obj.data
    if mesh.uv_layers.active is None:
        return ""
    digest = hashlib.sha256(b"remi-uv-validation-v2-gap-between-islands")
    for collection, attribute, width, dtype in (
        (mesh.vertices, "co", 3, np.float32),
        (mesh.loops, "vertex_index", 1, np.int32),
        (mesh.polygons, "loop_total", 1, np.int32),
        (mesh.polygons, "material_index", 1, np.int32),
        (mesh.edges, "use_seam", 1, np.bool_),
        (mesh.uv_layers.active.data, "uv", 2, np.float32),
    ):
        values = np.empty(len(collection) * width, dtype=dtype)
        collection.foreach_get(attribute, values)
        digest.update(values.tobytes())
    digest.update(mesh.uv_layers.active.name.encode("utf-8"))
    digest.update(np.asarray(obj.matrix_world, dtype=np.float64).tobytes())
    return digest.hexdigest()


def validate_existing_uv(obj):
    """Validate an existing map without changing it or running an unwrap."""
    if obj.data.uv_layers.active is None:
        return None
    if bpy.context.mode == "EDIT_MESH":
        obj.update_from_editmode()
    if obj.get("remi_uv_fingerprint") == _uv_fingerprint(obj):
        try:
            stats = UVStats(**json.loads(obj.get("remi_uv_validation", "{}")))
            if stats.valid:
                return stats
        except (ValueError, TypeError):
            pass
    analysis = analyze_mesh(obj.data)
    return evaluate_uv(obj.data, analysis, _seams_from_active_uv(obj.data), check_overlaps=True)


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


def _capture_candidate(mesh, name: str, seams: set[int], stats: UVStats, packer: str, warnings=None):
    return _UVCandidate(name, _capture_uvs(mesh), set(seams), stats, packer, list(warnings or []))


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
    obj, mesh, analysis, seams, profile, solver, texture_size, margin_px, stats, warnings,
) -> tuple[UVStats, set[int]]:
    """Cut conflicting faces and repack without re-flattening good regions."""
    from .metrics import _chart_ids

    for _ in range(max(3, profile.repair_passes)):
        if not stats.overlap_pairs:
            break
        details = find_uv_overlaps(mesh, max_pairs=4096)
        charts, _count = _chart_ids(analysis, seams)
        intra = [
            (d["polygon_a"], d["polygon_b"]) for d in details
            if charts[d["polygon_a"]] == charts[d["polygon_b"]]
        ]
        additions, faces = add_overlap_split_seams(analysis, intra, seams)
        if len(faces) > max(64, stats.triangle_count // 8):
            break
        # Selected neighboring faces must also be separated from each other.
        additions.update(e.index for e in analysis.edges if any(f in faces for f in e.faces))
        old_uvs, old_seams = _capture_uvs(mesh), set(seams)
        candidate_seams = seams | additions
        try:
            _apply_seams(mesh, candidate_seams, False)
            _pack(obj, analysis, candidate_seams, profile, texture_size, margin_px, conservative=True)
            candidate = evaluate_uv(mesh, analysis, candidate_seams, True, texture_size, margin_px)
            progress = candidate.overlap_pairs_complete and (
                not stats.overlap_pairs_complete or candidate.overlap_pairs < stats.overlap_pairs
            )
            if (
                not progress or candidate.collapsed_triangles or candidate.flipped_triangles
                or candidate.non_finite_uvs
            ):
                _restore_uvs(mesh, old_uvs)
                _apply_seams(mesh, old_seams, False)
                break
            warnings.append(
                f"Cut {len(faces)} conflicting faces without re-unwrapping other charts; "
                f"{candidate.overlap_pairs} intersections remain"
            )
            stats, seams = candidate, candidate_seams
        except (RuntimeError, ValueError):
            _restore_uvs(mesh, old_uvs)
            _apply_seams(mesh, old_seams, False)
            break
    return stats, seams


def _unwrap_local_faces(obj, faces, method, iterations):
    """Reparameterize only an edited region, retaining its total texture area."""
    _object_mode()
    mesh = obj.data
    previous = _capture_uvs(mesh)
    mesh.calc_loop_triangles()
    loops = {loop for face in faces for loop in mesh.polygons[face].loop_indices}
    triangles = [tuple(t.loops) for t in mesh.loop_triangles if t.polygon_index in faces]

    def area(coordinates):
        p = np.asarray(coordinates)[np.asarray(triangles)]
        a, b = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
        return np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() * 0.5

    before_area = area(previous)
    for vertex in mesh.vertices:
        vertex.select = False
    for edge in mesh.edges:
        edge.select = False
    for face in mesh.polygons:
        face.select = face.index in faces
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        result = bpy.ops.uv.unwrap(
            method=method, fill_holes=True, correct_aspect=True, margin=0.0,
            no_flip=True, iterations=min(18, iterations),
        )
        if "FINISHED" not in result:
            raise RuntimeError("Local unwrap failed")
    finally:
        _object_mode()
    current = np.asarray(_capture_uvs(mesh))
    after_area = area(current)
    if not np.isfinite(current).all() or after_area <= 1.0e-15:
        raise RuntimeError("Local unwrap collapsed or produced invalid coordinates")
    indices = np.asarray(sorted(loops))
    center = current[indices].mean(axis=0)
    current[indices] = center + (current[indices] - center) * math.sqrt(before_area / after_area)
    for index in range(len(current)):
        if index not in loops:
            current[index] = previous[index]
    _restore_uvs(mesh, current)


def _project_stretched_triangles(mesh, analysis, seams, stats, limit):
    """Give a small number of badly stretched triangles their own isometric charts."""
    faces = {
        face for face, stretch in stats.face_distortion.items()
        if stretch > limit and len(mesh.polygons[face].vertices) == 3
    }
    if not faces or len(faces) > max(32, stats.triangle_count // 200):
        return None
    total_area = sum(face.area for face in mesh.polygons)
    scale = math.sqrt(stats.packing_occupancy / max(total_area, 1.0e-20))
    for ordinal, face_index in enumerate(sorted(faces)):
        face = mesh.polygons[face_index]
        points = [mesh.vertices[v].co for v in face.vertices]
        x = (points[1] - points[0]).normalized()
        normal = (points[1] - points[0]).cross(points[2] - points[0]).normalized()
        y = normal.cross(x)
        for loop_index, point in zip(face.loop_indices, points):
            delta = point - points[0]
            mesh.uv_layers.active.data[loop_index].uv = (
                2.0 + ordinal + delta.dot(x) * scale, delta.dot(y) * scale,
            )
    seams = seams | {e.index for e in analysis.edges if any(f in faces for f in e.faces)}
    _apply_seams(mesh, seams, False)
    return seams, len(faces)


def ensure_remi_uv(
    obj: bpy.types.Object,
    profile_id: str = "NORMAL_BAKE",
    texture_size: int = 2048,
    margin_px: int = 4,
    preserve_existing_seams: bool = True,
    replace_existing: bool = False,
    trust_stored_result: bool = False,
) -> UVResult:
    """Generate, refine and verify a UV atlas, preserving input on failure."""
    if obj is None or obj.type != "MESH" or not obj.data.polygons:
        return UVResult(False, error="Remi UV needs a mesh with faces")
    profile = get_profile(profile_id)
    mesh = obj.data
    if bpy.context.mode == "EDIT_MESH":
        obj.update_from_editmode()
    if mesh.uv_layers.active and not replace_existing:
        cached = bool(
            trust_stored_result and obj.get("remi_uv_fingerprint") == _uv_fingerprint(obj)
            and obj.get("remi_uv_texture_size") == int(texture_size)
            and obj.get("remi_uv_margin_px") == int(margin_px)
            and obj.get("remi_uv_profile") == profile.identifier
        )
        stats = validate_existing_uv(obj)
        if stats and stats.valid:
            return UVResult(
                True, created=False, profile=profile.identifier,
                classification=str(obj.get("remi_uv_classification", "")),
                solver=str(obj.get("remi_uv_solver", "EXISTING")) if cached else "EXISTING",
                chart_count=stats.chart_count, stats=stats,
                warnings=["Reused the unchanged validated UV result" if cached else "Kept the existing validated UV map"],
            )
    warnings = ["Existing UV map failed validation and was regenerated"] if mesh.uv_layers.active and not replace_existing else []
    snapshot = _ContextSnapshot(bpy.context, obj)
    original_seams = {e.index for e in mesh.edges if e.use_seam}
    original_layer = mesh.uv_layers.active
    original_name = original_layer.name if original_layer else None
    original_uvs = _capture_uvs(mesh) if original_layer else None
    original_render = [layer.active_render for layer in mesh.uv_layers]
    success = False
    candidates = []
    failures = []
    analysis = None
    try:
        snapshot.prepare()
        generated = set(obj.get("remi_uv_generated_seams", ()))
        artist = original_seams - generated if preserve_existing_seams else set()
        _apply_seams(mesh, artist, False)
        analysis = analyze_mesh(mesh)
        mandatory = artist | {
            e.index for e in analysis.edges
            if (e.non_manifold and not e.boundary)
            or (profile.preserve_material_boundaries and e.material_boundary)
            or (profile.preserve_sharp_edges and e.sharp)
        }
        if mesh.uv_layers.active is None:
            mesh.uv_layers.new(name="RemiUV")
        mesh.calc_loop_triangles()
        large = len(mesh.loop_triangles) >= _LARGE_MESH_TRIANGLE_THRESHOLD

        def consider(name, seams, solver="CONFORMAL", repack=True):
            seams = set(seams) | mandatory
            _apply_seams(mesh, seams, False)
            local_warnings = []
            stats = evaluate_uv(mesh, analysis, seams, True)
            if stats.flipped_triangles and not stats.collapsed_triangles:
                stats, seams, _ = _repair_uv_flips(
                    obj, mesh, analysis, seams, profile, texture_size, margin_px,
                    stats, mandatory, local_warnings,
                )
            if stats.non_finite_uvs or stats.collapsed_triangles or stats.flipped_triangles:
                failures.append((name, stats))
                return
            if repack:
                packer = _pack(obj, analysis, seams, profile, texture_size, margin_px)
            else:
                uvs, triangles, charts, _ = _input_arrays(mesh, analysis, seams)
                _restore_uvs(mesh, fit_islands(uvs, triangles, charts, texture_size, margin_px))
                packer = name
            stats = evaluate_uv(mesh, analysis, seams, True, texture_size, margin_px)
            if stats.overlap_pairs:
                stats, seams = _repair_uv_overlaps(
                    obj, mesh, analysis, seams, profile, solver,
                    texture_size, margin_px, stats, local_warnings,
                )
                stats = evaluate_uv(mesh, analysis, seams, True, texture_size, margin_px)
            if stats.valid:
                base = _capture_candidate(mesh, name, seams, stats, packer, local_warnings)
                candidates.append(base)
                projected = _project_stretched_triangles(mesh, analysis, seams, stats, profile.stretch_limit)
                if projected:
                    projected_seams, count = projected
                    try:
                        projected_packer = _pack(obj, analysis, projected_seams, profile, texture_size, margin_px)
                        projected_stats = evaluate_uv(mesh, analysis, projected_seams, True, texture_size, margin_px)
                        if projected_stats.valid:
                            candidates.append(_capture_candidate(
                                mesh, name + "_LOCAL_STRETCH", projected_seams, projected_stats, projected_packer,
                                local_warnings + [f"Reprojected {count} severely stretched triangles as local charts"],
                            ))
                    except (RuntimeError, ValueError):
                        pass
                    finally:
                        _restore_candidate(mesh, base)
            else:
                failures.append((name, stats))
            print(f"Remi UV candidate {name}: {'valid' if stats.valid else 'rejected'}, "
                  f"{stats.chart_count} charts, {stats.packing_occupancy:.1%} coverage", flush=True)

        # Independent native recovery is eligible even when another route fails.
        # Artist constraints require a constrained unwrap instead of re-charting.
        if not artist:
            try:
                native = unwrap_candidates(mesh, analysis, texture_size, margin_px)
                for attempt in native:
                    apply_packing_attempt(mesh, attempt)
                    consider(("XATLAS_LARGE_MESH+" if large else "") + attempt.name,
                             _seams_from_active_uv(mesh))
            except (RuntimeError, ValueError) as error:
                warnings.append(f"Native charting could not complete: {error}")

        if not large or not candidates:
            _apply_seams(mesh, artist, False)
            seams = generate_seams(mesh, analysis, profile, preserve_existing=False) | mandatory
            for solver in dict.fromkeys((profile.solver, "ANGLE_BASED", "CONFORMAL")):
                try:
                    _apply_seams(mesh, seams, False)
                    _run_unwrap(obj, solver, profile.iterations)
                    preliminary = evaluate_uv(mesh, analysis, seams, False)
                    # Split coherent high-stretch regions as independent proposals;
                    # packed candidates are retained before a new unwrap is tried.
                    consider("REMI_" + solver, seams, solver)
                    if preliminary.conformal_p95 > profile.stretch_limit:
                        bad = {f for f, value in preliminary.face_distortion.items() if value > profile.stretch_limit}
                        split = seams | add_distortion_split_seams(analysis, bad, seams)
                        if split != seams:
                            _apply_seams(mesh, split, False)
                            _run_unwrap(obj, solver, profile.iterations)
                            consider("REMI_SPLIT_" + solver, split, solver)
                    if not preliminary.collapsed_triangles and not preliminary.flipped_triangles and not preliminary.non_finite_uvs:
                        break
                except (RuntimeError, ValueError) as error:
                    warnings.append(f"{solver} candidate failed: {error}")
            if not artist:
                try:
                    _apply_seams(mesh, mandatory, False)
                    _smart_fallback(obj, texture_size, margin_px)
                    smart_seams = _smart_candidate_seams(mesh, analysis, profile, mandatory)
                    raw = _capture_uvs(mesh)
                    # Keep an already well-spaced native Smart layout eligible.
                    consider("SMART_NATIVE_PACK", smart_seams, repack=False)
                    _restore_uvs(mesh, raw)
                    consider("SMART_XATLAS_PACK", smart_seams)
                except (RuntimeError, ValueError) as error:
                    warnings.append(f"Smart candidate failed: {error}")

        if not candidates:
            least = min(failures, key=lambda item: (item[1].collapsed_triangles, item[1].flipped_triangles, item[1].overlap_pairs)) if failures else None
            stats = least[1] if least else None
            detail = (
                f"{stats.collapsed_triangles} collapsed, {stats.flipped_triangles} flipped, "
                f"{'at least ' if not stats.overlap_pairs_complete else ''}{stats.overlap_pairs} overlaps; "
                f"gap {'passed' if stats.gap_valid else 'failed or unverified'}"
            ) if stats else "no parameterization candidate completed"
            return UVResult(False, profile=profile.identifier, stats=stats, warnings=warnings,
                            error="UV validation failed: " + detail)

        # Coverage must not win by stretching an otherwise square checkerboard
        # or changing relative texture resolution. Compare only near the best
        # available shape and density quality before scoring coverage.
        least_stretch = min(c.stats.conformal_p95 for c in candidates)
        density_error = lambda stats: max(abs(1.0 - stats.density_p05), abs(stats.density_p95 - 1.0))
        least_density_error = min(density_error(c.stats) for c in candidates)
        qualified = [c for c in candidates if
            c.stats.conformal_p95 <= max(1.05, least_stretch * 1.10)
            and density_error(c.stats) <= max(0.15, least_density_error * 1.10)
        ]
        if qualified:
            least_maximum = min(c.stats.conformal_max for c in qualified)
            qualified = [c for c in qualified if c.stats.conformal_max <= max(profile.stretch_limit, least_maximum * 1.10)]
        best = max(qualified, key=_candidate_rank) if qualified else min(
            candidates, key=lambda c: (c.stats.conformal_p95, density_error(c.stats)),
        )
        _restore_candidate(mesh, best)
        warnings.extend(best.warnings)
        refined_uvs, refined_seams, stats, trace = refine_atlas(
            mesh, analysis, best.seams, profile, texture_size, margin_px, mandatory,
            lambda faces: _unwrap_local_faces(obj, faces, profile.solver, profile.iterations),
        )
        _restore_uvs(mesh, refined_uvs)
        _apply_seams(mesh, refined_seams, False)
        # Always verify the actual float32 Blender output, including large meshes.
        stats = evaluate_uv(mesh, analysis, refined_seams, True, texture_size, margin_px)
        if not stats.valid:
            _restore_candidate(mesh, best)
            refined_seams, stats = best.seams, best.stats
            trace = []
        warnings.extend("UV refinement: " + message for message in trace)
        mesh.uv_layers.active.name = "RemiUV"
        mesh.uv_layers.active.active_render = True
        solver = best.name + "+" + best.packer + ("+REFINED" if trace else "")
        _store_summary(obj, profile.identifier, analysis.classification, solver, stats,
                       refined_seams - artist, texture_size, margin_px)
        success = True
        print(f"Remi UV: '{obj.name}' {stats.chart_count} charts, {stats.packing_occupancy:.1%} coverage, "
              f"{stats.overlap_pairs} overlaps, minimum gap {stats.minimum_gap_px} px", flush=True)
        return UVResult(True, created=True, profile=profile.identifier,
                        classification=analysis.classification, solver=solver,
                        chart_count=stats.chart_count, stats=stats, warnings=warnings)
    except (RuntimeError, ValueError) as error:
        return UVResult(False, profile=profile.identifier, error=str(error), warnings=warnings)
    finally:
        _object_mode()
        if not success:
            if original_uvs is not None:
                _restore_uvs(mesh, original_uvs)
                mesh.uv_layers.active.name = original_name
                for layer, render in zip(mesh.uv_layers, original_render):
                    layer.active_render = render
            elif mesh.uv_layers.active:
                mesh.uv_layers.remove(mesh.uv_layers.active)
            _apply_seams(mesh, original_seams, False)
        snapshot.restore()
