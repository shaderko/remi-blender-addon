"""Pixel-aware UV island packing backed by the vendored xatlas library."""

from dataclasses import dataclass
import time

import numpy as np

from .metrics import _chart_ids

try:
    from ._native import pack_uvs as _native_pack_uvs, unwrap_mesh as _native_unwrap_mesh
except (ImportError, ModuleNotFoundError):
    _native_pack_uvs = None
    _native_unwrap_mesh = None


@dataclass(frozen=True)
class PackingAttempt:
    name: str
    uvs: np.ndarray
    occupancy: float
    xatlas_utilization: float
    width: int
    height: int
    chart_count: int
    duration_ms: float


def native_packer_available() -> bool:
    return _native_pack_uvs is not None and _native_unwrap_mesh is not None


def _input_arrays(mesh, analysis, seams: set[int]):
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        raise RuntimeError("The mesh has no active UV layer")
    mesh.calc_loop_triangles()
    uvs = np.asarray([tuple(loop.uv) for loop in uv_layer.data], dtype=np.float32)
    triangles = np.asarray(
        [tuple(triangle.loops) for triangle in mesh.loop_triangles],
        dtype=np.uint32,
    )
    face_charts, chart_count = _chart_ids(analysis, seams)
    chart_ids = np.asarray(
        [face_charts[triangle.polygon_index] for triangle in mesh.loop_triangles],
        dtype=np.uint32,
    )
    return uvs, triangles, chart_ids, chart_count


def _triangle_occupancy(uvs: np.ndarray, triangles: np.ndarray) -> float:
    points = uvs[triangles]
    edge_a = points[:, 1] - points[:, 0]
    edge_b = points[:, 2] - points[:, 0]
    area = 0.5 * np.abs(edge_a[:, 0] * edge_b[:, 1] - edge_a[:, 1] * edge_b[:, 0]).sum()
    return min(1.0, float(area))


def pack_candidates(
    mesh,
    analysis,
    seams: set[int],
    texture_size: int,
    margin_px: int,
    conservative: bool = False,
) -> list[PackingAttempt]:
    """Pack the current charts and return independently measured candidates."""
    if _native_pack_uvs is None:
        return []
    uvs, triangles, chart_ids, chart_count = _input_arrays(mesh, analysis, seams)
    resolution = max(16, int(texture_size))
    padding = max(0, int(margin_px))

    configurations = [
        ("XATLAS_AXIS", False, True, True, False),
        ("XATLAS_FREE", False, True, False, False),
    ]
    if conservative:
        configurations = [("XATLAS_BLOCK", False, True, True, True)] + configurations
    elif chart_count <= 8 and resolution <= 512:
        configurations.append(("XATLAS_EXACT", True, True, True, False))

    attempts = []
    for name, brute_force, rotate, rotate_to_axis, block_align in configurations:
        started = time.perf_counter()
        result = _native_pack_uvs(
            uvs,
            triangles,
            chart_ids,
            resolution,
            padding,
            brute_force,
            rotate,
            rotate_to_axis,
            True,
            block_align,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        output = np.asarray(result["uvs"], dtype=np.float32)
        atlas_indices = np.asarray(result["atlas_indices"])
        if int(result["atlas_count"]) != 1 or np.any(atlas_indices != 0):
            continue
        if not np.isfinite(output).all():
            continue
        minimum = np.min(output, axis=0)
        maximum = np.max(output, axis=0)
        if np.any(minimum < -1.0e-6) or np.any(maximum > 1.000001):
            continue
        attempts.append(PackingAttempt(
            name=name,
            uvs=output,
            occupancy=_triangle_occupancy(output, triangles),
            xatlas_utilization=float(result["utilization"]),
            width=int(result["width"]),
            height=int(result["height"]),
            chart_count=int(result["chart_count"]),
            duration_ms=elapsed_ms,
        ))
    return sorted(
        attempts,
        key=lambda attempt: (attempt.occupancy, attempt.xatlas_utilization),
        reverse=True,
    )


def unwrap_candidates(
    mesh,
    analysis,
    texture_size: int,
    margin_px: int,
) -> list[PackingAttempt]:
    """Generate, parameterize, and pack alternative charts directly in xatlas."""
    if _native_unwrap_mesh is None:
        return []
    mesh.calc_loop_triangles()
    positions = np.asarray([tuple(vertex.co) for vertex in mesh.vertices], dtype=np.float32)
    vertex_triangles = np.asarray(
        [tuple(triangle.vertices) for triangle in mesh.loop_triangles],
        dtype=np.uint32,
    )
    loop_triangles = np.asarray(
        [tuple(triangle.loops) for triangle in mesh.loop_triangles],
        dtype=np.uint32,
    )
    materials = np.asarray(
        [mesh.polygons[triangle.polygon_index].material_index for triangle in mesh.loop_triangles],
        dtype=np.uint32,
    )
    if analysis.classification in {"HARD_SURFACE", "IRREGULAR"}:
        max_costs = (1.5, 3.0)
    elif analysis.classification in {"PLANAR", "CYLINDRICAL"}:
        max_costs = (2.5, 4.0)
    else:
        max_costs = (2.0, 3.5)

    attempts = []
    for max_cost in max_costs:
        started = time.perf_counter()
        result = _native_unwrap_mesh(
            positions,
            vertex_triangles,
            materials,
            max(16, int(texture_size)),
            max(0, int(margin_px)),
            max_cost,
            2,
            False,
            True,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if int(result["atlas_count"]) != 1:
            print(
                f"Remi UV: {max_cost:g} xatlas chart cost produced "
                f"{int(result['atlas_count'])} atlases; candidate rejected"
            )
            continue
        triangle_atlases = np.asarray(result["triangle_atlas_indices"])
        if np.any(triangle_atlases != 0):
            print(
                f"Remi UV: {max_cost:g} xatlas chart cost left "
                f"{int(np.count_nonzero(triangle_atlases != 0))} triangles unpacked; "
                "candidate rejected"
            )
            continue
        corner_uvs = np.asarray(result["corner_uvs"], dtype=np.float32)
        output = np.full((len(mesh.loops), 2), np.nan, dtype=np.float32)
        compatible = True
        for triangle_index, loop_indices in enumerate(loop_triangles):
            for corner, loop_index in enumerate(loop_indices):
                uv = corner_uvs[triangle_index, corner]
                if np.isfinite(output[loop_index]).all():
                    if np.max(np.abs(output[loop_index] - uv)) > 1.0e-5:
                        compatible = False
                        break
                else:
                    output[loop_index] = uv
            if not compatible:
                break
        if not compatible or not np.isfinite(output).all():
            print(
                f"Remi UV: {max_cost:g} xatlas chart cost split a Blender ngon "
                "internally; candidate rejected"
            )
            continue
        attempt = PackingAttempt(
            name=f"XATLAS_CHARTS_C{max_cost:g}",
            uvs=output,
            occupancy=_triangle_occupancy(output, loop_triangles),
            xatlas_utilization=float(result["utilization"]),
            width=int(result["width"]),
            height=int(result["height"]),
            chart_count=int(result["chart_count"]),
            duration_ms=elapsed_ms,
        )
        attempts.append(attempt)
        print(
            f"Remi UV: {attempt.name} generated {attempt.chart_count} charts, "
            f"{attempt.occupancy:.1%} geometry occupancy, "
            f"{attempt.duration_ms:.0f} ms"
        )
    return attempts


def apply_packing_attempt(mesh, attempt: PackingAttempt):
    uv_layer = mesh.uv_layers.active
    if uv_layer is None or len(uv_layer.data) != len(attempt.uvs):
        raise RuntimeError("Packed UV output does not match the Blender loop count")
    for loop, uv in zip(uv_layer.data, attempt.uvs):
        loop.uv = (float(uv[0]), float(uv[1]))
    mesh.update()
