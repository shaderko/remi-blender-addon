"""Geometry analysis and deterministic seam/chart generation for Remi UV."""

from dataclasses import dataclass
import math
from statistics import median

from mathutils import Vector

from .settings import UVProfile


_EPS = 1.0e-12


@dataclass(frozen=True)
class EdgeFeature:
    index: int
    faces: tuple[int, ...]
    angle: float
    length: float
    boundary: bool
    non_manifold: bool
    material_boundary: bool
    existing_seam: bool
    sharp: bool


@dataclass
class MeshAnalysis:
    classification: str
    components: list[list[int]]
    face_component: list[int]
    component_closed: list[bool]
    edges: list[EdgeFeature]
    face_directions: list[int]
    pca_axes: tuple[Vector, Vector, Vector]
    pca_values: tuple[float, float, float]
    center: Vector
    normal_variance: float
    sharp_edge_ratio: float
    triangle_ratio: float
    quad_ratio: float


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * max(0.0, min(1.0, fraction))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _pca(vertices) -> tuple[Vector, tuple[Vector, Vector, Vector], tuple[float, float, float]]:
    """Return centroid and stable principal axes without requiring SciPy."""
    if not vertices:
        axes = (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0)))
        return Vector((0.0, 0.0, 0.0)), axes, (0.0, 0.0, 0.0)

    center = sum((vertex.co for vertex in vertices), Vector()) / len(vertices)
    try:
        import numpy as np

        coords = np.asarray([tuple(vertex.co - center) for vertex in vertices], dtype=float)
        covariance = np.dot(coords.T, coords) / max(1, len(coords))
        values, vectors = np.linalg.eigh(covariance)
        order = np.argsort(values)[::-1]
        result_axes = []
        result_values = []
        for index in order:
            axis = Vector(vectors[:, index].tolist()).normalized()
            # Resolve eigenvector sign deterministically for repeatable charts.
            largest = max(range(3), key=lambda component: abs(axis[component]))
            if axis[largest] < 0.0:
                axis.negate()
            result_axes.append(axis)
            result_values.append(max(0.0, float(values[index])))
        # Force a right-handed basis after deterministic sign selection.
        if result_axes[0].cross(result_axes[1]).dot(result_axes[2]) < 0.0:
            result_axes[2].negate()
        return center, tuple(result_axes), tuple(result_values)
    except (ImportError, ValueError, TypeError):
        axes = (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0)))
        return center, axes, (0.0, 0.0, 0.0)


def _face_components(polygons, edge_faces: dict[tuple[int, int], list[int]]):
    adjacency = [set() for _ in polygons]
    for faces in edge_faces.values():
        if len(faces) == 2:
            a, b = faces
            adjacency[a].add(b)
            adjacency[b].add(a)

    components = []
    face_component = [-1] * len(polygons)
    for start in range(len(polygons)):
        if face_component[start] >= 0:
            continue
        component_index = len(components)
        stack = [start]
        faces = []
        face_component[start] = component_index
        while stack:
            face = stack.pop()
            faces.append(face)
            for neighbor in adjacency[face]:
                if face_component[neighbor] < 0:
                    face_component[neighbor] = component_index
                    stack.append(neighbor)
        components.append(faces)
    return components, face_component


def analyze_mesh(mesh) -> MeshAnalysis:
    """Extract topology and geometric signals used by chart generation."""
    if not mesh.polygons:
        raise ValueError("The mesh has no faces")

    mesh.update()
    center, axes, pca_values = _pca(mesh.vertices)
    edge_lookup = {
        tuple(sorted((edge.vertices[0], edge.vertices[1]))): edge.index
        for edge in mesh.edges
    }
    edge_faces: dict[tuple[int, int], list[int]] = {key: [] for key in edge_lookup}
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            edge_faces.setdefault(tuple(sorted(key)), []).append(polygon.index)

    components, face_component = _face_components(mesh.polygons, edge_faces)
    component_closed = [True] * len(components)
    edge_features: list[EdgeFeature] = [None] * len(mesh.edges)
    total_edge_length = 0.0
    sharp_edge_length = 0.0
    interior_angles = []

    for key, edge_index in edge_lookup.items():
        edge = mesh.edges[edge_index]
        faces = tuple(edge_faces.get(key, ()))
        length = (mesh.vertices[key[0]].co - mesh.vertices[key[1]].co).length
        total_edge_length += length
        boundary = len(faces) == 1
        non_manifold = len(faces) != 2
        if faces:
            component_index = face_component[faces[0]]
            if boundary or len(faces) > 2:
                component_closed[component_index] = False
        if len(faces) == 2:
            normal_a = mesh.polygons[faces[0]].normal
            normal_b = mesh.polygons[faces[1]].normal
            angle = normal_a.angle(normal_b, 0.0)
            interior_angles.append(angle)
            material_boundary = (
                mesh.polygons[faces[0]].material_index
                != mesh.polygons[faces[1]].material_index
            )
        else:
            angle = math.pi
            material_boundary = False
        sharp = bool(getattr(edge, "use_edge_sharp", False))
        if angle >= math.radians(45.0) or sharp:
            sharp_edge_length += length
        edge_features[edge_index] = EdgeFeature(
            index=edge_index,
            faces=faces,
            angle=angle,
            length=length,
            boundary=boundary,
            non_manifold=non_manifold,
            material_boundary=material_boundary,
            existing_seam=bool(edge.use_seam),
            sharp=sharp,
        )

    total_area = sum(max(polygon.area, _EPS) for polygon in mesh.polygons)
    normal_sum = sum(
        (polygon.normal * max(polygon.area, _EPS) for polygon in mesh.polygons),
        Vector(),
    )
    normal_variance = 1.0 - min(1.0, normal_sum.length / max(total_area, _EPS))
    triangle_ratio = sum(polygon.loop_total == 3 for polygon in mesh.polygons) / len(mesh.polygons)
    quad_ratio = sum(polygon.loop_total == 4 for polygon in mesh.polygons) / len(mesh.polygons)
    sharp_edge_ratio = sharp_edge_length / max(total_edge_length, _EPS)
    angle_median = median(interior_angles) if interior_angles else 0.0
    angle_p90 = _percentile(interior_angles, 0.90)

    major, secondary, tertiary = axes
    primary_value, secondary_value, tertiary_value = pca_values
    axial_normals = [abs(polygon.normal.dot(major)) for polygon in mesh.polygons]
    side_ratio = sum(value < 0.35 for value in axial_normals) / len(axial_normals)
    value_ratio = primary_value / max(secondary_value, _EPS)
    radial_ratio = secondary_value / max(tertiary_value, _EPS)

    has_non_manifold_edges = any(
        feature.non_manifold and not feature.boundary
        for feature in edge_features
    )

    if normal_variance < 0.035:
        classification = "PLANAR"
    elif has_non_manifold_edges:
        classification = "IRREGULAR"
    elif len(components) == 1 and value_ratio > 1.45 and radial_ratio < 2.2 and side_ratio > 0.55:
        classification = "CYLINDRICAL"
    elif sharp_edge_ratio > 0.12 or (
        sharp_edge_ratio > 0.035 and angle_p90 > math.radians(35.0)
    ):
        classification = "HARD_SURFACE"
    elif triangle_ratio > 0.80 and angle_p90 > math.radians(30.0) and angle_median > math.radians(5.0):
        classification = "IRREGULAR"
    else:
        classification = "ORGANIC"

    face_directions = []
    signed_axes = (major, -major, secondary, -secondary, tertiary, -tertiary)
    for polygon in mesh.polygons:
        face_directions.append(max(range(6), key=lambda index: polygon.normal.dot(signed_axes[index])))

    return MeshAnalysis(
        classification=classification,
        components=components,
        face_component=face_component,
        component_closed=component_closed,
        edges=edge_features,
        face_directions=face_directions,
        pca_axes=axes,
        pca_values=pca_values,
        center=center,
        normal_variance=normal_variance,
        sharp_edge_ratio=sharp_edge_ratio,
        triangle_ratio=triangle_ratio,
        quad_ratio=quad_ratio,
    )


def _cylindrical_seams(mesh, analysis: MeshAnalysis) -> set[int]:
    """Create cap boundaries plus a single hidden longitudinal seam."""
    major, secondary, _tertiary = analysis.pca_axes
    seams = set()
    longitudinal = []
    for feature in analysis.edges:
        if len(feature.faces) != 2:
            continue
        face_a, face_b = feature.faces
        axial_a = abs(mesh.polygons[face_a].normal.dot(major))
        axial_b = abs(mesh.polygons[face_b].normal.dot(major))
        cap_a = axial_a > 0.65
        cap_b = axial_b > 0.65
        if cap_a != cap_b:
            seams.add(feature.index)
            continue
        if cap_a or cap_b:
            continue
        edge = mesh.edges[feature.index]
        point_a = mesh.vertices[edge.vertices[0]].co
        point_b = mesh.vertices[edge.vertices[1]].co
        direction = point_b - point_a
        if direction.length_squared <= _EPS:
            continue
        direction.normalize()
        if abs(direction.dot(major)) < 0.72:
            continue
        midpoint = (point_a + point_b) * 0.5 - analysis.center
        radial = midpoint - major * midpoint.dot(major)
        if radial.length_squared <= _EPS:
            continue
        radial.normalize()
        longitudinal.append((radial.dot(secondary), radial, feature.index))

    if longitudinal:
        _score, target, _edge_index = min(longitudinal, key=lambda item: item[0])
        cosine_tolerance = math.cos(math.radians(12.0))
        selected = [item[2] for item in longitudinal if item[1].dot(target) >= cosine_tolerance]
        seams.update(selected or [_edge_index])
    return seams


def generate_seams(
    mesh,
    analysis: MeshAnalysis,
    profile: UVProfile,
    preserve_existing: bool = True,
) -> set[int]:
    """Generate chart boundaries from topology, materials, shape, and profile."""
    seams = set()
    threshold = math.radians(profile.seam_angle_degrees)

    if analysis.classification == "HARD_SURFACE":
        threshold = min(threshold, math.radians(48.0))
    elif analysis.classification == "IRREGULAR":
        threshold = min(threshold, math.radians(58.0))

    for feature in analysis.edges:
        if preserve_existing and feature.existing_seam:
            seams.add(feature.index)
        if feature.non_manifold and not feature.boundary:
            seams.add(feature.index)
        if profile.preserve_sharp_edges and feature.sharp:
            seams.add(feature.index)
        if profile.preserve_material_boundaries and feature.material_boundary:
            seams.add(feature.index)
        if len(feature.faces) == 2 and feature.angle >= threshold:
            seams.add(feature.index)

    if analysis.classification == "CYLINDRICAL":
        seams.update(_cylindrical_seams(mesh, analysis))

    # Smooth closed shells and noisy scans need explicit disk-like charts even
    # when every local dihedral is small. PCA directional regions provide a
    # deterministic OBB-style candidate without shredding open planar panels.
    directional_components = {
        index
        for index, closed in enumerate(analysis.component_closed)
        if closed
    }
    if analysis.classification == "IRREGULAR":
        directional_components.update(range(len(analysis.components)))

    if profile.use_directional_charts and analysis.classification != "CYLINDRICAL":
        for feature in analysis.edges:
            if len(feature.faces) != 2:
                continue
            face_a, face_b = feature.faces
            component = analysis.face_component[face_a]
            if component not in directional_components:
                continue
            if analysis.face_directions[face_a] != analysis.face_directions[face_b]:
                seams.add(feature.index)

    return seams


def add_distortion_split_seams(
    analysis: MeshAnalysis,
    bad_faces: set[int],
    current_seams: set[int],
) -> set[int]:
    """Split coherent high-distortion regions from their lower-stretch neighbors."""
    if not bad_faces:
        return set()
    additions = set()
    for feature in analysis.edges:
        if len(feature.faces) != 2 or feature.index in current_seams:
            continue
        a_bad = feature.faces[0] in bad_faces
        b_bad = feature.faces[1] in bad_faces
        if a_bad != b_bad:
            additions.add(feature.index)
    return additions


def add_overlap_split_seams(
    analysis: MeshAnalysis,
    overlap_face_pairs: list[tuple[int, int]],
    current_seams: set[int],
) -> tuple[set[int], set[int]]:
    """Cut a small face cover that resolves intra-island UV intersections.

    Overlap pairs form a graph. Choosing every involved face creates excessive
    tiny islands, so a deterministic greedy vertex cover selects faces that
    resolve the most collisions first. Their union is separated as one or more
    coherent repair charts.
    """
    remaining = {
        (min(face_a, face_b), max(face_a, face_b))
        for face_a, face_b in overlap_face_pairs
        if face_a != face_b
    }
    selected_faces = set()
    while remaining:
        degree = {}
        for face_a, face_b in remaining:
            degree[face_a] = degree.get(face_a, 0) + 1
            degree[face_b] = degree.get(face_b, 0) + 1
        selected = min(
            degree,
            key=lambda face: (-degree[face], face),
        )
        selected_faces.add(selected)
        remaining = {
            pair for pair in remaining
            if selected not in pair
        }

    additions = set()
    for feature in analysis.edges:
        if feature.index in current_seams or not feature.faces:
            continue
        selected_count = sum(face in selected_faces for face in feature.faces)
        if selected_count and selected_count != len(feature.faces):
            additions.add(feature.index)
        elif len(feature.faces) == 1 and selected_count:
            additions.add(feature.index)
    return additions, selected_faces


def count_charts(analysis: MeshAnalysis, seams: set[int]) -> int:
    """Count connected face regions after cutting the generated seam set."""
    adjacency = [set() for _ in analysis.face_component]
    for feature in analysis.edges:
        if len(feature.faces) != 2 or feature.index in seams:
            continue
        a, b = feature.faces
        adjacency[a].add(b)
        adjacency[b].add(a)

    visited = set()
    charts = 0
    for start in range(len(adjacency)):
        if start in visited:
            continue
        charts += 1
        stack = [start]
        visited.add(start)
        while stack:
            face = stack.pop()
            for neighbor in adjacency[face]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
    return charts
