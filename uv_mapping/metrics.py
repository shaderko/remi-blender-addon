"""UV validity, overlap, distortion, and packing metrics."""

from dataclasses import asdict, dataclass, field
import math
from statistics import median


_EPS = 1.0e-10


@dataclass
class UVStats:
    triangle_count: int = 0
    chart_count: int = 0
    collapsed_triangles: int = 0
    degenerate_3d_triangles: int = 0
    flipped_triangles: int = 0
    overlap_pairs: int = 0
    non_finite_uvs: int = 0
    conformal_median: float = 1.0
    conformal_p95: float = 1.0
    conformal_max: float = 1.0
    packing_occupancy: float = 0.0
    uv_bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    face_distortion: dict[int, float] = field(default_factory=dict)
    flipped_faces: list[int] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any((
            self.collapsed_triangles,
            self.flipped_triangles,
            self.overlap_pairs,
            self.non_finite_uvs,
        ))

    def to_dict(self) -> dict:
        result = asdict(self)
        result["valid"] = self.valid
        return result


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * max(0.0, min(1.0, fraction))
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _chart_ids(analysis, seams: set[int]) -> tuple[list[int], int]:
    adjacency = [set() for _ in analysis.face_component]
    for feature in analysis.edges:
        if len(feature.faces) != 2 or feature.index in seams:
            continue
        a, b = feature.faces
        adjacency[a].add(b)
        adjacency[b].add(a)

    result = [-1] * len(adjacency)
    chart = 0
    for start in range(len(adjacency)):
        if result[start] >= 0:
            continue
        result[start] = chart
        stack = [start]
        while stack:
            face = stack.pop()
            for neighbor in adjacency[face]:
                if result[neighbor] < 0:
                    result[neighbor] = chart
                    stack.append(neighbor)
        chart += 1
    return result, chart


def _triangle_distortion(points, uvs):
    """Return (conformal ratio, signed UV area) for one 3D/UV triangle."""
    edge_1 = points[1] - points[0]
    edge_2 = points[2] - points[0]
    length_1 = edge_1.length
    area_3d_twice = edge_1.cross(edge_2).length
    if length_1 <= _EPS or area_3d_twice <= _EPS:
        return None, 0.0

    axis_x = edge_1 / length_1
    x_2 = edge_2.dot(axis_x)
    y_2 = area_3d_twice / length_1
    uv_1_x = uvs[1][0] - uvs[0][0]
    uv_1_y = uvs[1][1] - uvs[0][1]
    uv_2_x = uvs[2][0] - uvs[0][0]
    uv_2_y = uvs[2][1] - uvs[0][1]
    signed_area_twice = uv_1_x * uv_2_y - uv_1_y * uv_2_x

    j00 = uv_1_x / length_1
    j01 = (uv_2_x - uv_1_x * x_2 / length_1) / y_2
    j10 = uv_1_y / length_1
    j11 = (uv_2_y - uv_1_y * x_2 / length_1) / y_2

    # Eigenvalues of J^T J are squared singular values.
    a = j00 * j00 + j10 * j10
    b = j00 * j01 + j10 * j11
    d = j01 * j01 + j11 * j11
    trace = a + d
    discriminant = math.sqrt(max(0.0, (a - d) * (a - d) + 4.0 * b * b))
    lambda_max = max(0.0, (trace + discriminant) * 0.5)
    lambda_min = max(0.0, (trace - discriminant) * 0.5)
    if lambda_min <= _EPS or not math.isfinite(lambda_max):
        return math.inf, signed_area_twice * 0.5
    return math.sqrt(lambda_max / lambda_min), signed_area_twice * 0.5


def _strict_triangle_overlap(a, b, epsilon=_EPS) -> bool:
    """Use the separating-axis theorem; boundary-only contact is permitted."""
    for triangle in (a, b):
        for index in range(3):
            p = triangle[index]
            q = triangle[(index + 1) % 3]
            axis_x = -(q[1] - p[1])
            axis_y = q[0] - p[0]
            a_values = [point[0] * axis_x + point[1] * axis_y for point in a]
            b_values = [point[0] * axis_x + point[1] * axis_y for point in b]
            overlap = min(max(a_values), max(b_values)) - max(min(a_values), min(b_values))
            if overlap <= epsilon:
                return False
    return True


def _overlap_pairs(triangles) -> list[tuple[int, int]]:
    if len(triangles) < 2:
        return []
    min_u = min(point[0] for triangle in triangles for point in triangle[0])
    min_v = min(point[1] for triangle in triangles for point in triangle[0])
    max_u = max(point[0] for triangle in triangles for point in triangle[0])
    max_v = max(point[1] for triangle in triangles for point in triangle[0])
    span_u = max(max_u - min_u, _EPS)
    span_v = max(max_v - min_v, _EPS)
    grid_size = max(8, min(256, int(math.ceil(math.sqrt(len(triangles))))))
    buckets: dict[tuple[int, int], list[int]] = {}

    for index, (uvs, _vertices, _polygon) in enumerate(triangles):
        tri_min_u = min(point[0] for point in uvs)
        tri_max_u = max(point[0] for point in uvs)
        tri_min_v = min(point[1] for point in uvs)
        tri_max_v = max(point[1] for point in uvs)
        x0 = max(0, min(grid_size - 1, int((tri_min_u - min_u) / span_u * grid_size)))
        x1 = max(0, min(grid_size - 1, int((tri_max_u - min_u) / span_u * grid_size)))
        y0 = max(0, min(grid_size - 1, int((tri_min_v - min_v) / span_v * grid_size)))
        y1 = max(0, min(grid_size - 1, int((tri_max_v - min_v) / span_v * grid_size)))
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                buckets.setdefault((x, y), []).append(index)

    candidates = set()
    for indices in buckets.values():
        for offset, a in enumerate(indices):
            for b in indices[offset + 1:]:
                candidates.add((min(a, b), max(a, b)))

    overlaps = []
    for a, b in candidates:
        uvs_a, vertices_a, polygon_a = triangles[a]
        uvs_b, vertices_b, polygon_b = triangles[b]
        if polygon_a == polygon_b or len(set(vertices_a).intersection(vertices_b)) >= 2:
            continue
        if _strict_triangle_overlap(uvs_a, uvs_b):
            overlaps.append((a, b))
    return overlaps


def _overlap_count(triangles) -> int:
    return len(_overlap_pairs(triangles))


def find_uv_overlaps(mesh) -> list[dict]:
    """Return exact triangle pairs reported by the active UV validator.

    This intentionally exposes polygon, vertex, loop, and UV information so a
    failed production mesh can be diagnosed without guessing from a count.
    """
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return []
    mesh.calc_loop_triangles()
    triangles = []
    loop_indices_by_triangle = []
    for triangle in mesh.loop_triangles:
        loop_indices = tuple(triangle.loops)
        triangles.append((
            tuple(tuple(uv_layer.data[index].uv) for index in loop_indices),
            tuple(triangle.vertices),
            triangle.polygon_index,
        ))
        loop_indices_by_triangle.append(loop_indices)

    details = []
    for a, b in _overlap_pairs(triangles):
        uvs_a, vertices_a, polygon_a = triangles[a]
        uvs_b, vertices_b, polygon_b = triangles[b]
        details.append({
            "triangle_a": a,
            "triangle_b": b,
            "polygon_a": polygon_a,
            "polygon_b": polygon_b,
            "vertices_a": vertices_a,
            "vertices_b": vertices_b,
            "loops_a": loop_indices_by_triangle[a],
            "loops_b": loop_indices_by_triangle[b],
            "uvs_a": uvs_a,
            "uvs_b": uvs_b,
        })
    return details


def evaluate_uv(mesh, analysis, seams: set[int], check_overlaps: bool = True) -> UVStats:
    """Measure the active UV layer and return production-oriented diagnostics."""
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        stats = UVStats(non_finite_uvs=1)
        return stats

    mesh.calc_loop_triangles()
    chart_ids, chart_count = _chart_ids(analysis, seams)
    distortions = []
    face_distortion = {}
    triangles_2d = []
    signs_by_chart: dict[int, list[tuple[float, float, int]]] = {}
    collapsed = 0
    degenerate_3d = 0
    non_finite = 0
    uv_area = 0.0
    all_uvs = []

    for triangle in mesh.loop_triangles:
        loop_indices = tuple(triangle.loops)
        vertex_indices = tuple(triangle.vertices)
        points = [mesh.vertices[index].co for index in vertex_indices]
        uvs = [tuple(uv_layer.data[index].uv) for index in loop_indices]
        if not all(math.isfinite(value) for uv in uvs for value in uv):
            non_finite += 1
            continue
        all_uvs.extend(uvs)
        distortion, signed_area = _triangle_distortion(points, uvs)
        if distortion is None:
            degenerate_3d += 1
            continue
        if abs(signed_area) <= _EPS or not math.isfinite(distortion):
            collapsed += 1
        else:
            distortions.append(distortion)
            polygon_index = triangle.polygon_index
            face_distortion[polygon_index] = max(
                distortion,
                face_distortion.get(polygon_index, 1.0),
            )
            chart = chart_ids[polygon_index]
            signs_by_chart.setdefault(chart, []).append((
                signed_area,
                abs(signed_area),
                polygon_index,
            ))
            uv_area += abs(signed_area)
        triangles_2d.append((tuple(uvs), vertex_indices, triangle.polygon_index))

    flipped = 0
    flipped_faces = set()
    for signed_areas in signs_by_chart.values():
        orientation = 1.0 if sum(
            math.copysign(weight, area)
            for area, weight, _polygon in signed_areas
        ) >= 0.0 else -1.0
        for area, _weight, polygon in signed_areas:
            if area * orientation < -_EPS:
                flipped += 1
                flipped_faces.add(polygon)

    if all_uvs:
        min_u = min(uv[0] for uv in all_uvs)
        min_v = min(uv[1] for uv in all_uvs)
        max_u = max(uv[0] for uv in all_uvs)
        max_v = max(uv[1] for uv in all_uvs)
        bounds = (min_u, min_v, max_u, max_v)
        # UV0..1 has unit area. Measuring against the island bounds would hide
        # empty border strips and overstate the density artists see in the tile.
        occupancy = min(1.0, uv_area)
    else:
        bounds = (0.0, 0.0, 0.0, 0.0)
        occupancy = 0.0

    return UVStats(
        triangle_count=len(mesh.loop_triangles),
        chart_count=chart_count,
        collapsed_triangles=collapsed,
        degenerate_3d_triangles=degenerate_3d,
        flipped_triangles=flipped,
        overlap_pairs=_overlap_count(triangles_2d) if check_overlaps else 0,
        non_finite_uvs=non_finite,
        conformal_median=median(distortions) if distortions else math.inf,
        conformal_p95=_percentile(distortions, 0.95) if distortions else math.inf,
        conformal_max=max(distortions, default=math.inf),
        packing_occupancy=occupancy,
        uv_bounds=bounds,
        face_distortion=face_distortion,
        flipped_faces=sorted(flipped_faces),
    )
