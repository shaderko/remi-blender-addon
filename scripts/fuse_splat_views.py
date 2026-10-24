"""Reconstruct a surface from posed splat views using TSDF and/or Poisson.

Run with the external Python environment that has Open3D/NumPy:
  python scripts/fuse_splat_views.py --views path/to/views --archive path/to/splats.npz

Inputs are the RGB + normalized-depth views produced by render_splat_views.py.
TSDF uses only rendered geometry. Poisson is an independent baseline built from
Gaussian centers and their shortest-axis normals. Both are evaluated against the
splat centers; the script does not claim ground-truth accuracy.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import cv2
import numpy as np
import open3d as o3d
import trimesh


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=("GRID", "FUSED", "POISSON", "TSDF", "BOTH"), default="GRID")
    parser.add_argument("--min-opacity", type=float, default=0.08)
    parser.add_argument("--voxel-divisor", type=float, default=160.0,
                        help="voxel size = bounds diameter / divisor")
    parser.add_argument("--tsdf-trunc-voxels", type=float, default=4.0)
    parser.add_argument("--smooth-iterations", type=int, default=2)
    parser.add_argument("--target-triangles", type=int, default=180000)
    parser.add_argument("--poisson-depth", type=int, default=9)
    parser.add_argument("--max-points", type=int, default=120000)
    parser.add_argument("--depth-stride", type=int, default=2)
    parser.add_argument("--max-fused-points", type=int, default=250000)
    parser.add_argument("--include-splats", action="store_true",
                        help="add Gaussian centers/normals to the fused depth points")
    parser.add_argument("--grid-resolution", type=int, default=160)
    parser.add_argument("--grid-chunk", type=int, default=8)
    parser.add_argument("--grid-smooth", type=float, default=0.8)
    parser.add_argument("--min-views", type=float, default=1.5,
                        help="minimum accumulated observation weight for a voxel")
    parser.add_argument("--trunc-divisor", type=float, default=35.0,
                        help="TSDF truncation = bounds radius / divisor")
    parser.add_argument("--depth-median", type=int, default=0,
                        help="odd median-filter size for depth maps before fusion")
    parser.add_argument("--depth-filter", choices=("NONE", "MEDIAN", "OUTLIER"), default="NONE")
    parser.add_argument("--depth-outlier-threshold", type=float, default=0.006)
    parser.add_argument("--robust-passes", type=int, default=1,
                        help="second pass rejects views that disagree with the first-pass SDF")
    parser.add_argument("--robust-threshold", type=float, default=0.004)
    parser.add_argument("--vertex-color", action="store_true",
                        help="project view colors onto reconstructed vertices")
    return parser.parse_args()


def load_views(root):
    metadata = json.loads((root / "views.json").read_text(encoding="utf-8"))
    return metadata


def load_rgbd(view_root, view, far, alpha_threshold=0.5, depth_median=0,
              depth_filter="NONE", outlier_threshold=0.006):
    view_dir = view_root / f"view_{int(view['index']):04d}"
    rgb_path = view_dir / view["rgb"]
    depth_path = view_dir / view["depth"]
    rgb = cv2.imread(str(rgb_path), cv2.IMREAD_UNCHANGED)
    depth16 = cv2.imread(str(depth_path), cv2.IMREAD_UNCHANGED)
    if rgb is None or depth16 is None:
        raise FileNotFoundError(rgb_path if rgb is None else depth_path)
    if rgb.ndim == 2:
        rgb = cv2.cvtColor(rgb, cv2.COLOR_GRAY2BGRA)
    if rgb.shape[2] == 3:
        alpha = np.ones(rgb.shape[:2], dtype=np.uint8) * 255
        rgb = np.dstack((rgb, alpha))
    alpha = rgb[..., 3]
    color = cv2.cvtColor(rgb, cv2.COLOR_BGRA2RGB)
    normalized = depth16.astype(np.float64) / 65535.0
    # Blender saves the 16-bit depth pass through the display transform. The
    # render script uses Standard/sRGB, so decode the transfer function before
    # converting normalized depth back to scene units.
    linear = np.where(
        normalized <= 0.04045, normalized / 12.92,
        ((normalized + 0.055) / 1.055) ** 2.4,
    )
    depth = (linear * far).astype(np.float32)
    depth[alpha < alpha_threshold * 255] = 0.0
    # The Gaussian renderer can leave transparent gsplat borders opaque at the
    # far plane. They are not object geometry.
    depth[depth >= far * 0.98] = 0.0
    if depth_filter != "NONE" or (depth_median and depth_median > 1):
        from scipy.ndimage import median_filter
        valid = depth > 0
        size = int(depth_median) if depth_median and depth_median > 1 else 3
        filtered = median_filter(
            np.where(valid, depth, far).astype(np.float32),
            size=size, mode="nearest",
        )
        if depth_filter == "OUTLIER":
            residual = np.abs(depth - filtered)
            replace = valid & (residual > outlier_threshold)
            depth[replace] = filtered[replace]
        else:
            depth[valid] = filtered[valid]
    color[alpha < alpha_threshold * 255] = 0
    return color, depth, alpha


def clean_trimesh(vertices, faces, bounds, args, do_smooth=True):
    """Mesh cleanup without Open3D's mesh kernels (which abort on this host)."""
    mesh = trimesh.Trimesh(vertices=np.asarray(vertices), faces=np.asarray(faces), process=True)
    mesh.remove_unreferenced_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    low = np.asarray(bounds["low"]) - (np.asarray(bounds["high"]) - np.asarray(bounds["low"])) * 0.05
    high = np.asarray(bounds["high"]) + (np.asarray(bounds["high"]) - np.asarray(bounds["low"])) * 0.05
    inside = np.all((mesh.vertices >= low) & (mesh.vertices <= high), axis=1)
    face_inside = inside[mesh.faces].all(axis=1)
    mesh.update_faces(face_inside)
    mesh.remove_unreferenced_vertices()
    if len(mesh.faces):
        parts = mesh.split(only_watertight=False)
        if len(parts) > 1:
            minimum = max(32, int(len(mesh.faces) * 0.002))
            parts = [part for part in parts if len(part.faces) >= minimum]
            if parts:
                mesh = trimesh.util.concatenate(parts)
    if do_smooth and args.smooth_iterations > 0 and len(mesh.faces):
        mesh = trimesh.smoothing.filter_taubin(
            mesh, lamb=0.5, nu=-0.53, iterations=args.smooth_iterations,
        )
    if len(mesh.faces) > args.target_triangles:
        try:
            mesh = mesh.simplify_quadric_decimation(face_count=args.target_triangles)
        except (ImportError, ModuleNotFoundError, ValueError):
            pass
    mesh.fix_normals()
    return mesh


def clean_mesh(mesh, bounds, args, do_smooth=True):
    if mesh.is_empty():
        raise RuntimeError("Reconstruction produced an empty mesh")
    vertices = np.asarray(mesh.vertices)
    low, high = bounds["low"], bounds["high"]
    pad = (np.asarray(high) - np.asarray(low)) * 0.05
    bbox = o3d.geometry.AxisAlignedBoundingBox(
        np.asarray(low) - pad, np.asarray(high) + pad,
    )
    print("Clean: crop", flush=True)
    mesh = mesh.crop(bbox)
    if mesh.is_empty():
        raise RuntimeError("Cropping removed the entire mesh")
    print("Clean: basic removal", flush=True)
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_duplicated_vertices()
    mesh.remove_unreferenced_vertices()
    print("Clean: clusters", flush=True)
    clusters, counts, areas = mesh.cluster_connected_triangles()
    counts = np.asarray(counts)
    if len(counts) > 1:
        keep = counts >= max(32, int(counts.sum() * 0.002))
        mesh.remove_triangles_by_mask(~keep[np.asarray(clusters)])
        mesh.remove_unreferenced_vertices()
    if do_smooth and args.smooth_iterations > 0 and not mesh.is_empty():
        print("Clean: smooth", flush=True)
        mesh = mesh.filter_smooth_taubin(
            number_of_iterations=args.smooth_iterations, lambda_filter=0.5, mu=-0.53,
        )
    if len(mesh.triangles) > args.target_triangles:
        print("Clean: decimate", flush=True)
        mesh = mesh.simplify_quadric_decimation(args.target_triangles)
    mesh.compute_vertex_normals()
    return mesh


def tsdf_mesh(metadata, view_root, bounds, args):
    radius = float(bounds["radius"])
    diameter = radius * 2.0
    voxel = max(diameter / args.voxel_divisor, 1e-5)
    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=voxel,
        sdf_trunc=voxel * args.tsdf_trunc_voxels,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )
    integrated = []
    for view in metadata["views"]:
        far = float(view["far"])
        color, depth, alpha = load_rgbd(view_root, view, far)
        if not np.any(depth > 0):
            continue
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            o3d.geometry.Image(np.ascontiguousarray(color)),
            o3d.geometry.Image(np.ascontiguousarray(depth)),
            depth_scale=1.0,
            depth_trunc=far,
            convert_rgb_to_intensity=False,
        )
        intrinsic = np.asarray(view["intrinsics"], dtype=np.float64)
        fx, cx, fy, cy = intrinsic[0, 0], intrinsic[0, 2], intrinsic[1, 1], intrinsic[1, 2]
        camera = o3d.camera.PinholeCameraIntrinsic(
            int(depth.shape[1]), int(depth.shape[0]), fx, fy, cx, cy,
        )
        extrinsic = np.asarray(view["world_to_camera_opencv"], dtype=np.float64)
        volume.integrate(rgbd, camera, extrinsic)
        integrated.append(view["index"])
    print(f"TSDF integrated {len(integrated)}/{len(metadata['views'])} views", flush=True)
    mesh = volume.extract_triangle_mesh()
    cleaned = clean_trimesh(
        np.asarray(mesh.vertices), np.asarray(mesh.triangles), bounds, args, do_smooth=True,
    )
    return cleaned, {
        "voxel_size": voxel, "integrated_views": len(integrated),
    }


def grid_tsdf_mesh(metadata, view_root, bounds, args):
    """A small, dependency-light TSDF fusion + marching-cubes implementation.

    Open3D's ScalableTSDFVolume crashes on this Windows/NumPy combination, so
    the projection/update and isosurface are implemented with NumPy and
    scikit-image. It is deliberately bounded to the splat bounding box.
    """
    from scipy.ndimage import gaussian_filter
    from skimage import measure

    radius = float(bounds["radius"])
    low = np.asarray(bounds["low"], dtype=np.float64) - radius * 0.10
    high = np.asarray(bounds["high"], dtype=np.float64) + radius * 0.10
    resolution = max(64, int(args.grid_resolution))
    spacing = (high - low) / (resolution - 1)
    trunc = max(radius / float(args.trunc_divisor), float(np.max(spacing)) * 1.5)
    xs, ys, zs = (
        np.linspace(low[0], high[0], resolution),
        np.linspace(low[1], high[1], resolution),
        np.linspace(low[2], high[2], resolution),
    )
    def project(reference=None, threshold=0.0):
        tsdf_sum = np.zeros((resolution, resolution, resolution), dtype=np.float32)
        weight_sum = np.zeros_like(tsdf_sum)
        views_used = 0
        for view_index, view in enumerate(metadata["views"]):
            print(f"TSDF projection pass view {view_index + 1}/{len(metadata['views'])}", flush=True)
            color, depth, alpha = load_rgbd(
                view_root, view, float(view["far"]), depth_median=args.depth_median,
                depth_filter=args.depth_filter,
                outlier_threshold=args.depth_outlier_threshold,
            )
            if not np.any(depth > 0):
                continue
            camera = np.asarray(view["world_to_camera_opencv"], dtype=np.float64)
            intrinsics = np.asarray(view["intrinsics"], dtype=np.float64)
            fx, cx, fy, cy = intrinsics[0, 0], intrinsics[0, 2], intrinsics[1, 1], intrinsics[1, 2]
            view_weight = 0
            for x0 in range(0, resolution, max(1, int(args.grid_chunk))):
                x1 = min(resolution, x0 + max(1, int(args.grid_chunk)))
                gx, gy, gz = np.meshgrid(xs[x0:x1], ys, zs, indexing="ij")
                points = np.stack((gx, gy, gz), axis=-1).reshape(-1, 3)
                camera_points = points @ camera[:3, :3].T + camera[:3, 3]
                z = camera_points[:, 2]
                valid = (z > 1e-5) & (z < float(view["far"]) * 0.98)
                u = np.rint(fx * camera_points[:, 0] / np.maximum(z, 1e-8) + cx).astype(np.int64)
                v = np.rint(fy * camera_points[:, 1] / np.maximum(z, 1e-8) + cy).astype(np.int64)
                valid &= (u >= 0) & (u < depth.shape[1]) & (v >= 0) & (v < depth.shape[0])
                indices = np.flatnonzero(valid)
                if not len(indices):
                    continue
                sampled_depth = depth[v[indices], u[indices]]
                sampled_alpha = alpha[v[indices], u[indices]]
                sdf = sampled_depth - z[indices]
                keep = (
                    (sampled_alpha > 127)
                    & (sampled_depth > 0)
                    & (np.abs(sdf) < trunc)
                )
                if reference is not None:
                    reference_values = reference.reshape(-1)[indices]
                    keep &= ~np.isfinite(reference_values) | (
                        np.abs(sdf - reference_values) < threshold
                    )
                indices = indices[keep]
                if not len(indices):
                    continue
                weights = sampled_alpha[keep].astype(np.float64) / 255.0
                flat = indices
                chunk = (x1 - x0) * resolution * resolution
                tsdf_sum[x0:x1] += np.bincount(
                    flat, weights=sdf[keep] * weights, minlength=chunk,
                ).reshape(x1 - x0, resolution, resolution).astype(np.float32)
                weight_sum[x0:x1] += np.bincount(
                    flat, weights=weights, minlength=chunk,
                ).reshape(x1 - x0, resolution, resolution).astype(np.float32)
                view_weight += int(len(indices))
            if view_weight:
                views_used += 1
        return tsdf_sum, weight_sum, views_used

    tsdf_sum, weight_sum, views_used = project()
    mean_sdf = np.where(
        weight_sum > 0, tsdf_sum / np.maximum(weight_sum, 1e-8), np.nan,
    ).astype(np.float32)
    if args.robust_passes >= 2:
        print("TSDF robust consensus pass", flush=True)
        tsdf_sum, weight_sum, views_used_robust = project(
            mean_sdf, max(0.001, float(args.robust_threshold)),
        )
        if weight_sum.sum() > 0:
            views_used = max(views_used, views_used_robust)
    print(f"TSDF projected {views_used} views; building isosurface", flush=True)
    occupied = weight_sum >= max(1.0, float(args.min_views))
    sdf = np.full_like(tsdf_sum, trunc, dtype=np.float32)
    sdf[occupied] = tsdf_sum[occupied] / weight_sum[occupied]
    np.save(view_root / "sdf_debug.npy", sdf)
    print(f"SDF range {np.nanmin(sdf):.4g}..{np.nanmax(sdf):.4g}", flush=True)
    if args.grid_smooth > 0:
        sdf = gaussian_filter(sdf, sigma=args.grid_smooth).astype(np.float32)
    print(f"Smoothed SDF range {np.nanmin(sdf):.4g}..{np.nanmax(sdf):.4g}", flush=True)
    if np.nanmin(sdf) >= 0 or np.nanmax(sdf) <= 0:
        raise RuntimeError("TSDF grid has no zero crossing; increase coverage or truncation")
    vertices, faces, _normals, _values = measure.marching_cubes(
        sdf, level=0.0, spacing=spacing,
    )
    print(f"Marching cubes: {len(vertices)} vertices, {len(faces)} faces", flush=True)
    vertices = vertices + low[None, :]
    cleaned = clean_trimesh(vertices, faces, bounds, args, do_smooth=True)
    return cleaned, {
        "grid_resolution": resolution,
        "voxel_size": float(np.max(spacing)),
        "truncation": float(trunc),
        "views": views_used,
        "occupied_voxels": int(occupied.sum()),
    }


def fused_points_mesh(metadata, view_root, archive, bounds, args):
    """Back-project confidence-filtered depth into an oriented point cloud.

    This is the post-hoc analogue of SuGaR/2DGS depth fusion: the depth maps
    come from the native splat renderer, not a learned stereo model. Poisson
    completes the surface and preserves thin parts better than a coarse TSDF.
    """
    radius = float(bounds["radius"])
    diameter = radius * 2.0
    voxel = max(diameter / args.voxel_divisor, 1e-5)
    all_points = []
    all_normals = []
    views_used = 0
    for view in metadata["views"]:
        color, depth, alpha = load_rgbd(view_root, view, float(view["far"]))
        valid = (depth > 0) & (alpha > 127)
        if not np.any(valid):
            continue
        height, width = depth.shape
        yy, xx = np.indices((height, width), dtype=np.float64)
        intrinsics = np.asarray(view["intrinsics"], dtype=np.float64)
        fx, cx, fy, cy = intrinsics[0, 0], intrinsics[0, 2], intrinsics[1, 1], intrinsics[1, 2]
        z = depth.astype(np.float64)
        points_camera = np.stack(
            ((xx - cx) * z / fx, (yy - cy) * z / fy, z), axis=-1,
        )
        # Central differences need both neighbours on the same surface.
        edge = np.zeros_like(valid)
        edge[1:-1, 1:-1] = (
            valid[1:-1, 1:-1] & valid[1:-1, :-2] & valid[1:-1, 2:]
            & valid[:-2, 1:-1] & valid[2:, 1:-1]
        )
        dx = np.zeros_like(points_camera)
        dy = np.zeros_like(points_camera)
        dx[:, 1:-1] = points_camera[:, 2:] - points_camera[:, :-2]
        dy[1:-1, :] = points_camera[2:, :] - points_camera[:-2, :]
        normals = np.cross(dx, dy)
        lengths = np.linalg.norm(normals, axis=-1)
        edge &= lengths > 1e-9
        normals[edge] /= lengths[edge, None]
        ys, xs = np.nonzero(edge)
        if not len(ys):
            continue
        stride = max(1, int(args.depth_stride))
        ys, xs = ys[::stride], xs[::stride]
        camera_to_world = np.asarray(view["camera_to_world_opencv"], dtype=np.float64)
        rotation = camera_to_world[:3, :3]
        translation = camera_to_world[:3, 3]
        points_world = points_camera[ys, xs] @ rotation.T + translation
        normals_world = normals[ys, xs] @ rotation.T
        camera_position = camera_to_world[:3, 3]
        facing = np.einsum("ij,ij->i", normals_world, camera_position[None] - points_world)
        normals_world[facing < 0] *= -1
        all_points.append(points_world)
        all_normals.append(normals_world)
        views_used += 1
    if not all_points:
        raise RuntimeError("No valid depth samples for fused reconstruction")
    points = np.concatenate(all_points, axis=0)
    normals = np.concatenate(all_normals, axis=0)
    if args.include_splats:
        splats = np.load(archive, allow_pickle=False)
        positions = splats["positions"].astype(np.float64)
        scales = splats["scales"].astype(np.float64)
        rotations = splats["rotations"].astype(np.float64)
        opacity = splats["opacities"].astype(np.float64)
        keep = opacity >= args.min_opacity
        positions, scales, rotations = positions[keep], scales[keep], rotations[keep]
        if len(positions) > args.max_points:
            positions = positions[np.linspace(0, len(positions) - 1, args.max_points, dtype=np.int64)]
            scales = scales[: len(positions)]
            rotations = rotations[: len(positions)]
        splat_normals = np.empty_like(positions)
        for i, (quat, scale) in enumerate(zip(rotations, scales)):
            splat_normals[i] = quat_to_matrix(quat)[:, int(np.argmin(scale))]
        lengths = np.linalg.norm(splat_normals, axis=1)
        valid = lengths > 1e-8
        points = np.concatenate((points, positions[valid]), axis=0)
        normals = np.concatenate((normals, splat_normals[valid] / lengths[valid, None]), axis=0)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    pcd.normals = o3d.utility.Vector3dVector(normals)
    pcd = pcd.voxel_down_sample(voxel)
    low = np.asarray(bounds["low"]) - radius * 0.15
    high = np.asarray(bounds["high"]) + radius * 0.15
    point_array = np.asarray(pcd.points)
    in_bounds = np.all((point_array >= low) & (point_array <= high), axis=1)
    if not np.all(in_bounds):
        pcd = pcd.select_by_index(np.flatnonzero(in_bounds))
    pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=16, std_ratio=2.5)
    if len(pcd.points) > args.max_fused_points:
        # Uniform random subsample; keep the cloud deterministic for debugging.
        rng = np.random.default_rng(20261024)
        indices = rng.choice(len(pcd.points), args.max_fused_points, replace=False)
        pcd = pcd.select_by_index(indices)
    point_array = np.asarray(pcd.points)
    normal_array = np.asarray(pcd.normals)
    finite = np.isfinite(point_array).all(axis=1) & np.isfinite(normal_array).all(axis=1)
    normal_lengths = np.linalg.norm(normal_array, axis=1)
    finite &= normal_lengths > 1e-7
    if not np.all(finite):
        pcd = pcd.select_by_index(np.flatnonzero(finite))
        normal_array = np.asarray(pcd.normals)
        normal_lengths = np.linalg.norm(normal_array, axis=1)
    pcd.normalize_normals()
    o3d.io.write_point_cloud(str(view_root / "fused_points_debug.ply"), pcd)
    print(f"Fused {len(points)} depth samples from {views_used} views -> "
          f"{len(pcd.points)} Poisson points; normal-length range "
          f"{normal_lengths.min():.4g}..{normal_lengths.max():.4g}", flush=True)
    if len(pcd.points) < 100:
        raise RuntimeError("Too few valid fused points for Poisson reconstruction")
    # Open3D 0.18 can abort in the threaded Poisson path on Windows/NumPy 2;
    # the single-threaded solver is stable and still completes in seconds.
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=args.poisson_depth, scale=1.05, linear_fit=False, n_threads=1,
    )
    densities = np.asarray(densities)
    if len(densities):
        mesh.remove_vertices_by_mask(densities < np.quantile(densities, 0.02))
    cleaned = clean_trimesh(
        np.asarray(mesh.vertices), np.asarray(mesh.triangles), bounds, args, do_smooth=True,
    )
    return cleaned, {
        "voxel_size": voxel,
        "depth_samples": int(len(points)),
        "poisson_points": int(len(pcd.points)),
        "views": views_used,
        "poisson_depth": args.poisson_depth,
    }


def quat_to_matrix(q):
    w, x, y, z = np.asarray(q, dtype=np.float64)
    n = w * w + x * x + y * y + z * z
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    wx, wy, wz = s * w * x, s * w * y, s * w * z
    xx, xy, xz = s * x * x, s * x * y, s * x * z
    yy, yz, zz = s * y * y, s * y * z, s * z * z
    return np.array([
        [1 - (yy + zz), xy - wz, xz + wy],
        [xy + wz, 1 - (xx + zz), yz - wx],
        [xz - wy, yz + wx, 1 - (xx + yy)],
    ])


def poisson_mesh(archive, bounds, args):
    data = np.load(archive, allow_pickle=False)
    positions = data["positions"].astype(np.float32)
    scales = data["scales"].astype(np.float32)
    rotations = data["rotations"].astype(np.float32)
    opacity = data["opacities"].astype(np.float32)
    keep = opacity >= args.min_opacity
    positions, scales, rotations, opacity = (
        positions[keep], scales[keep], rotations[keep], opacity[keep],
    )
    if len(positions) > args.max_points:
        # Deterministic density-aware subsample: retain strong, compact splats.
        score = opacity / np.maximum(np.max(scales, axis=1), 1e-6)
        order = np.argsort(-score)[: args.max_points]
        positions, scales, rotations = positions[order], scales[order], rotations[order]
    if len(positions) < 100:
        raise RuntimeError("Too few splats for Poisson reconstruction")
    normals = np.empty_like(positions)
    for i, (quat, scale) in enumerate(zip(rotations, scales)):
        rotation = quat_to_matrix(quat)
        normals[i] = rotation[:, int(np.argmin(scale))]
    norms = np.linalg.norm(normals, axis=1)
    valid = norms > 1e-8
    positions, normals = positions[valid], normals[valid] / norms[valid, None]
    center = np.asarray(bounds["center"], dtype=np.float32)
    flip = np.einsum("ij,ij->i", normals, center[None] - positions) < 0
    normals[flip] *= -1
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(positions)
    pcd.normals = o3d.utility.Vector3dVector(normals)
    pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=args.poisson_depth, scale=1.05, linear_fit=False, n_threads=1,
    )
    densities = np.asarray(densities)
    if len(densities):
        threshold = np.quantile(densities, 0.02)
        mesh.remove_vertices_by_mask(densities < threshold)
    cleaned = clean_trimesh(
        np.asarray(mesh.vertices), np.asarray(mesh.triangles), bounds, args, do_smooth=True,
    )
    return cleaned, {"poisson_depth": args.poisson_depth, "input_points": len(positions)}


def vertex_colors_from_views(mesh, metadata, view_root, bounds):
    vertices = np.asarray(mesh.vertices)
    normals = np.asarray(mesh.vertex_normals)
    colors = np.zeros((len(vertices), 3), dtype=np.float64)
    weights = np.zeros(len(vertices), dtype=np.float64)
    radius = float(bounds["radius"])
    tolerance = max(radius * 0.035, 1e-4)
    loaded = []
    for view in metadata["views"]:
        color, depth, alpha = load_rgbd(view_root, view, float(view["far"]))
        loaded.append((view, color, depth, alpha))
    for view, color, depth, alpha in loaded:
        w2c = np.asarray(view["world_to_camera_opencv"], dtype=np.float64)
        intrinsic = np.asarray(view["intrinsics"], dtype=np.float64)
        height, width = depth.shape
        homogeneous = np.column_stack((vertices, np.ones(len(vertices))))
        camera_points = (w2c @ homogeneous.T).T
        z = camera_points[:, 2]
        valid = z > 1e-6
        u = np.full(len(vertices), -1, dtype=np.int64)
        v = np.full(len(vertices), -1, dtype=np.int64)
        u[valid] = np.rint(intrinsic[0, 0] * camera_points[valid, 0] / z[valid] + intrinsic[0, 2]).astype(np.int64)
        v[valid] = np.rint(intrinsic[1, 1] * camera_points[valid, 1] / z[valid] + intrinsic[1, 2]).astype(np.int64)
        inside = valid & (u >= 0) & (u < width) & (v >= 0) & (v < height)
        idx = np.flatnonzero(inside)
        if not len(idx):
            continue
        sampled_depth = depth[v[idx], u[idx]]
        sampled_alpha = alpha[v[idx], u[idx]].astype(np.float64) / 255.0
        visible = sampled_alpha > 0.5
        visible &= np.abs(sampled_depth - z[idx]) < tolerance
        # A surface should only receive color from cameras it faces. Allowing
        # back-facing views mixes opposite sides of thin sails and produces the
        # speckled color seen in the first reconstruction.
        direction = np.asarray(view["position"])[None] - vertices[idx]
        direction_norm = np.linalg.norm(direction, axis=1)
        facing = np.einsum("ij,ij->i", normals[idx], direction) / np.maximum(direction_norm, 1e-8)
        visible &= facing > 0.08
        idx = idx[visible]
        if not len(idx):
            continue
        weight = np.clip(facing[visible], 0.0, 1.0) ** 2 * sampled_alpha[visible]
        colors[idx] += color[v[idx], u[idx]] * weight[:, None]
        weights[idx] += weight
    valid = weights > 0
    if np.any(valid):
        colors[valid] /= weights[valid, None]
    colors[~valid] = 180
    if isinstance(mesh, trimesh.Trimesh):
        rgba = np.column_stack((np.clip(colors, 0, 255), np.full(len(colors), 255)))
        mesh.visual.vertex_colors = rgba.astype(np.uint8)
    else:
        mesh.vertex_colors = o3d.utility.Vector3dVector(np.clip(colors / 255.0, 0, 1))
    return int(valid.sum()), len(vertices)


def metrics(mesh, archive, bounds):
    if isinstance(mesh, trimesh.Trimesh):
        vertices = np.asarray(mesh.vertices)
        triangles = np.asarray(mesh.faces)
        tm = mesh
    else:
        vertices = np.asarray(mesh.vertices)
        triangles = np.asarray(mesh.triangles)
        tm = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False)
    data = np.load(archive, allow_pickle=False)
    positions = data["positions"].astype(np.float64)
    opacity = data["opacities"].astype(np.float64)
    points = positions[opacity >= 0.08]
    if len(points) > 100000:
        points = points[np.linspace(0, len(points) - 1, 100000, dtype=np.int64)]
    if len(tm.faces) and len(points):
        try:
            closest, distance, _ = trimesh.proximity.closest_point(tm, points)
            distances = np.asarray(distance)
        except ModuleNotFoundError:
            # rtree is optional; sample the surface instead of exact closest
            # triangles for a dependency-free diagnostic metric.
            from scipy.spatial import cKDTree
            surface = tm.sample(max(20000, min(100000, len(tm.faces))))
            distances = cKDTree(surface).query(points, workers=-1)[0]
    else:
        distances = np.array([np.nan])
    components = len(tm.split(only_watertight=False)) if len(tm.faces) else 0
    return {
        "vertices": int(len(vertices)),
        "triangles": int(len(triangles)),
        "components": components,
        "watertight": bool(tm.is_watertight),
        "point_to_mesh_mean": float(np.nanmean(distances)),
        "point_to_mesh_median": float(np.nanmedian(distances)),
        "point_to_mesh_p95": float(np.nanpercentile(distances, 95)),
        "bbox_min": np.min(vertices, axis=0).tolist() if len(vertices) else None,
        "bbox_max": np.max(vertices, axis=0).tolist() if len(vertices) else None,
    }


def save(mesh, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(mesh, trimesh.Trimesh):
        mesh.export(str(path))
        mesh.export(str(path.with_suffix(".obj")))
    else:
        if not o3d.io.write_triangle_mesh(str(path), mesh, write_vertex_colors=True):
            raise RuntimeError(f"Failed to write {path}")
        o3d.io.write_triangle_mesh(str(path.with_suffix(".obj")), mesh)
    print(f"Wrote {path}", flush=True)


def main():
    args = parse_args()
    views_root = args.views.resolve()
    archive = args.archive.resolve()
    output = (args.output or views_root / "reconstruction").resolve()
    output.mkdir(parents=True, exist_ok=True)
    metadata = load_views(views_root)
    positions = np.load(archive, allow_pickle=False)["positions"]
    low, high = np.quantile(positions, [0.01, 0.99], axis=0)
    bounds = {"center": ((low + high) / 2).tolist(), "radius": float(np.max(high - low) / 2),
              "low": low.tolist(), "high": high.tolist()}
    report = {"input_views": len(metadata["views"]), "bounds": bounds, "methods": {}}
    methods = ("GRID", "POISSON") if args.mode == "BOTH" else (args.mode,)
    for method in methods:
        print(f"Building {method}", flush=True)
        if method == "TSDF":
            mesh, details = tsdf_mesh(metadata, views_root, bounds, args)
        elif method == "FUSED":
            mesh, details = fused_points_mesh(metadata, views_root, archive, bounds, args)
        elif method == "GRID":
            mesh, details = grid_tsdf_mesh(metadata, views_root, bounds, args)
        else:
            mesh, details = poisson_mesh(archive, bounds, args)
        if args.vertex_color:
            colored, total = vertex_colors_from_views(mesh, metadata, views_root, bounds)
            details["vertex_color_coverage"] = f"{colored}/{total}"
        details["metrics"] = metrics(mesh, archive, bounds)
        path = output / f"{method.lower()}.ply"
        save(mesh, path)
        details["path"] = str(path)
        report["methods"][method] = details
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"RECONSTRUCTION FAILED: {exc}", file=sys.stderr, flush=True)
        raise
