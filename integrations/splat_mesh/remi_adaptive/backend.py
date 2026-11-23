"""Native sparse evaluation and two-level surface extraction.

The frozen Gaussian-spacing threshold and original field recipe are preserved.
The shared library is built with the image/add-on, and never during a user job.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import ctypes
import hashlib
import platform
import time

import numpy as np
from scipy.spatial.transform import Rotation
from splatmesh import ExtractConfig, Mesh
from splatmesh.field import bounds_for

from .support import Config, GaussianSpacing


@dataclass(frozen=True)
class AdaptiveConfig:
    resolution: int = 256
    sparse_level: float = .03
    edge_tolerance: float = .35
    verify_coarse_placement: bool = False
    bounds: tuple | None = None
    force_fine: bool = False
    size_normalize: bool = True
    min_component_faces: int = 500
    taubin_iterations: int = 6

    def __post_init__(self):
        if (isinstance(self.resolution, bool) or not isinstance(self.resolution, int)
                or self.resolution < 16 or self.resolution % 8):
            raise ValueError("Resolution must be a multiple of 8, at least 16")
        if (not np.isfinite([self.sparse_level, self.edge_tolerance]).all()
                or self.sparse_level <= 0 or self.edge_tolerance < 0):
            raise ValueError("Invalid refinement settings")
        for value in (self.min_component_faces, self.taubin_iterations):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("Component threshold and smoothing must be nonnegative integers")
        if self.bounds is not None:
            low, high = (np.asarray(v, dtype=np.float64) for v in self.bounds)
            if (low.shape != (3,) or high.shape != (3,)
                    or not np.isfinite([low, high]).all() or np.any(high <= low)):
                raise ValueError("Invalid bounds")


_LIBRARY = None


def library():
    global _LIBRARY
    if _LIBRARY is not None:
        return _LIBRARY
    name = {"Darwin": "libremi_adaptive.dylib", "Linux": "libremi_adaptive.so",
            "Windows": "remi_adaptive.dll"}.get(platform.system())
    path = Path(__file__).with_name("_native") / (name or "unsupported")
    if not path.is_file():
        raise RuntimeError("The adaptive mesher native library is missing. Install a complete Remi "
                           "release, or build it with remi_adaptive/build_native.py before use.")
    lib = ctypes.CDLL(str(path))
    lib.remi_abi_version.argtypes = []
    lib.remi_abi_version.restype = ctypes.c_int
    lib.remi_source_sha256.argtypes = []
    lib.remi_source_sha256.restype = ctypes.c_char_p
    expected = hashlib.sha256(Path(__file__).with_name("adaptive.cpp").read_bytes()).hexdigest()
    if lib.remi_abi_version() != 1 or lib.remi_source_sha256().decode("ascii") != expected:
        raise RuntimeError("Adaptive mesher library does not match its source; rebuild the release")
    p = ctypes.c_void_p
    lib.remi_create.argtypes = [p] * 5 + [ctypes.c_int] + [p] * 2 + [ctypes.c_int] + [p] * 3 + [ctypes.c_int] + [ctypes.c_double] * 6
    lib.remi_create.restype = p
    lib.remi_coarse.argtypes = [p] * 3 + [ctypes.c_int]
    lib.remi_coarse.restype = ctypes.c_int
    lib.remi_extract.argtypes = [p, p, ctypes.c_double, ctypes.c_double, ctypes.c_int]
    lib.remi_extract.restype = ctypes.c_int
    lib.remi_cleanup.argtypes = [p, ctypes.c_int, ctypes.c_int]
    lib.remi_cleanup.restype = ctypes.c_int
    lib.remi_clean_arrays.argtypes = [p, p, ctypes.c_int64, ctypes.c_int64, ctypes.c_int, ctypes.c_int, p, p, p]
    lib.remi_clean_arrays.restype = ctypes.c_int
    lib.remi_profile.argtypes = [p, p]
    lib.remi_sizes.argtypes = [p, p]
    lib.remi_copy.argtypes = [p, p, p]
    lib.remi_free.argtypes = [p]
    for name in ("remi_profile", "remi_sizes", "remi_copy", "remi_free"):
        getattr(lib, name).restype = None
    _LIBRARY = lib
    return lib


def prepare(splats, cfg, threshold):
    start = time.process_time()
    if threshold.neighbors != 8 or threshold.stride != 2:
        raise ValueError("The native adaptive mesher requires 8 neighbors and stride 2")
    if (not np.isfinite(splats.opacities).all() or np.any(splats.opacities < 0)
            or np.any(splats.opacities > 1)):
        raise ValueError("Gaussian opacity must be finite and in [0, 1]")
    base = ExtractConfig(resolution=cfg.resolution, device="cpu")
    low, high = (bounds_for(splats, base.padding) if cfg.bounds is None else
                 (np.asarray(x, dtype=np.float64) for x in cfg.bounds))
    voxel = (high - low) / cfg.resolution
    scales = np.maximum(splats.scales, base.min_splat_sigma_voxels * float(voxel.min()))
    rotation = Rotation.from_quat(splats.rotations[:, [1, 2, 3, 0]]).as_matrix()
    weights = splats.opacities.copy()
    if cfg.size_normalize:
        tangent = np.sqrt(np.maximum(scales[:, 0] * scales[:, 1], 1e-12))
        weights *= np.median(tangent) / np.maximum(tangent, 1e-12)
    variance = np.einsum("nij,nj,nij->ni", rotation, scales ** 2, rotation)
    radius = np.clip(np.ceil(base.support_sigma * np.sqrt(np.maximum(variance, 0)) / voxel), 1, base.max_radius).astype(np.int32)
    inverse = np.einsum("nij,nj,nkj->nik", rotation, 1 / scales ** 2, rotation)
    metric = np.swapaxes(rotation, 1, 2) / scales[:, :, None]
    order = np.lexsort((radius[:, 2], radius[:, 1], radius[:, 0]))
    support = GaussianSpacing(splats, threshold)
    active = splats.opacities >= threshold.opacity_min
    arrays = [np.ascontiguousarray(a[order], dtype=np.float64) for a in
              [splats.positions, inverse, metric, weights]]
    arrays += [np.ascontiguousarray(radius[order]), np.ascontiguousarray(low), np.ascontiguousarray(voxel)]
    arrays += [np.ascontiguousarray(a, dtype=np.float64) for a in
               [splats.positions[active], support.spacing, splats.opacities[active]]]
    return arrays, support, time.process_time() - start


def reconstruct(splats, cfg=AdaptiveConfig(), threshold=Config(), *, progress=None, return_coarse=False):
    """Return (mesh, metrics, optional coarse samples). No I/O or compilation.

    Progress callbacks may raise InterruptedError; native calls release the GIL,
    and the worker process remains independently terminable during each stage.
    """
    start, wall = time.process_time(), time.perf_counter()
    notify = progress or (lambda _: None)
    notify(.02)
    lib = library()
    arrays, support, prep = prepare(splats, cfg, threshold)
    reference = support.reference
    _, _, _, _, _, low, voxel, active_points, spacing, opacity = arrays
    notify(.15)
    context = lib.remi_create(*(a.ctypes.data for a in arrays[:5]), len(splats),
                              low.ctypes.data, voxel.ctypes.data, cfg.resolution,
                              active_points.ctypes.data, spacing.ctypes.data, opacity.ctypes.data,
                              len(active_points), reference, threshold.level, threshold.minimum_level,
                              threshold.power, threshold.opacity_guard_low, threshold.opacity_guard_high)
    if not context:
        raise RuntimeError("Native context allocation failed")
    try:
        h = cfg.resolution // 2
        field = np.zeros((h,) * 3, np.float32)
        logs = np.zeros_like(field)
        stage = time.process_time()
        if lib.remi_coarse(context, field.ctypes.data, logs.ctypes.data, 1):
            raise RuntimeError("Coarse field stage failed")
        coarse_cpu = time.process_time() - stage
        notify(.4)
        stage = time.process_time()
        tolerance = cfg.edge_tolerance if cfg.verify_coarse_placement else 1.0
        if lib.remi_extract(context, logs.ctypes.data, cfg.sparse_level, tolerance, int(cfg.force_fine)):
            raise RuntimeError("Adaptive extraction failed")
        extract_cpu = time.process_time() - stage
        notify(.85)
        profile = np.zeros(6, np.float64)
        lib.remi_profile(context, profile.ctypes.data)
        sizes = np.zeros(8, np.int64)
        lib.remi_sizes(context, sizes.ctypes.data)
        raw_sizes = sizes.copy()
        stage = time.process_time()
        # Fan separation runs even when filtering/smoothing are disabled.
        if lib.remi_cleanup(context, cfg.min_component_faces, cfg.taubin_iterations):
            raise RuntimeError("Native cleanup failed")
        lib.remi_sizes(context, sizes.ctypes.data)
        cleanup_cpu = time.process_time() - stage
        vertices = np.empty((sizes[0], 3), np.float64)
        faces = np.empty((sizes[1], 3), np.int64)
        lib.remi_copy(context, vertices.ctypes.data, faces.ctypes.data)
    finally:
        lib.remi_free(context)
    mesh = Mesh(vertices, faces)
    notify(1.0)
    config_data = asdict(cfg)
    if cfg.bounds is not None:
        config_data["bounds"] = [np.asarray(v).tolist() for v in cfg.bounds]
    report = {
        "cpu_seconds": time.process_time() - start, "wall_seconds": time.perf_counter() - wall,
        "preparation_cpu_seconds": prep, "coarse_density_and_support_cpu_seconds": coarse_cpu,
        "adaptive_extraction_cpu_seconds": extract_cpu, "cleanup_cpu_seconds": cleanup_cpu,
        "native_stage_seconds": dict(zip(["interval_bounds", "cell_selection_and_validation", "contour_vertices",
                                           "contour_faces", "lazy_nearest_queries", "lazy_nearest_fallbacks"], profile.tolist())),
        "fine_density_node_queries": int(sizes[2]), "gaussian_exponent_evaluations": int(sizes[3]),
        "coarse_surface_leaves": int(sizes[4]), "fine_surface_leaves": int(sizes[5]),
        "multiple_component_leaves": int(sizes[6]), "refined_coarse_cells": int(sizes[7]),
        "raw_vertices": int(raw_sizes[0]), "raw_triangles": int(raw_sizes[1]),
        "vertices": len(vertices), "triangles": len(faces), "adaptive_config": config_data,
        "threshold_config": asdict(threshold), "reference_spacing": reference,
        "coarse_sample_count": h ** 3, "finest_grid_sample_count": cfg.resolution ** 3,
    }
    return mesh, report, (field, logs, low, voxel) if return_coarse else None


def native_clean(mesh, minimum=500, iterations=6):
    """Cleanup parity helper; the reconstruction path also separates vertex fans."""
    lib = library()
    v = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
    f = np.ascontiguousarray(mesh.faces, dtype=np.int64)
    outv, outf, sizes = np.empty_like(v), np.empty_like(f), np.zeros(2, np.int64)
    if lib.remi_clean_arrays(v.ctypes.data, f.ctypes.data, len(v), len(f), minimum, iterations,
                             outv.ctypes.data, outf.ctypes.data, sizes.ctypes.data):
        raise RuntimeError("Native cleanup failed")
    return Mesh(outv[:sizes[0]].copy(), outf[:sizes[1]].copy())
