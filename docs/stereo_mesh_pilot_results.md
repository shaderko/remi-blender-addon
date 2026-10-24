# Ship pilot: learned-stereo conversion is not ready to replace Create Mesh

Date: 2026-10-24. This is **one object**, not a general quality benchmark.

## Setup

- Existing `vehicle_pirate_ship` generation, seed 20261023, 65,536 Gaussians.
- Archive: `generations/20261024_044333_76cc230b/splats.npz`.
- Native Blender 5.3 beta EEVEE, 32 samples, 768 × 768 pixels.
- 48 rectified stereo pairs; 12 separate held-out cameras.
- Official DLNR Middlebury checkpoint, 16 iterations, full precision,
  regular PyTorch correlation, both matching directions, no custom CUDA build.
- DLNR inference: **57.9 s**, including loading/first-use overhead; peak PyTorch
  allocated VRAM **1.28 GiB**. This is not total driver VRAM or a speed guarantee.
- 224-resolution density baseline with shipped settings; learned-stereo TSDF
  at 256 and 384. No learned-stereo cleanup/smoothing/hole filling.

## Held-out reference-render agreement

Means over 12 held-out cameras. Reference is the source splat, **not ground-truth
geometry**. Missing/extra foreground are measured against the reference
foreground pixel count. Higher IoU is better; lower missing/extra is better.

| Method | Silhouette IoU | Missing foreground | Extra foreground | Triangles |
| --- | ---: | ---: | ---: | ---: |
| Density before component cleanup/mesh smoothing | 0.9663 | 0.40% | 3.07% | 1,003,624 |
| Shipped density | 0.9659 | 0.60% | 2.91% | 993,108 |
| Learned stereo, 256 | 0.9121 | 6.71% | 2.30% | 271,188 |
| Learned stereo, 384 | 0.8953 | 9.24% | 1.39% | 646,588 |

The stereo surface makes the broad hull smoother and has fewer triangles, but
loses rigging and sail-edge detail. Increasing fusion resolution alone makes
coverage worse: finer voxels/narrower world-space truncation expose more
cross-view depth disagreement rather than recover missing details.

The density cleanup ablation changes iso-level selection as well as cleanup
because the vendor's adaptive threshold depends on component filtering. It is
not an exact comparison of the same extracted surface before/after smoothing.

## Topology diagnostics

- Shipped density: 1 connected component, **2,294 non-manifold edges**, no
  boundary edges. No boundary edges does not mean a production-ready mesh or
  absence of unwanted handles/tunnels.
- Learned stereo, 256: 644 components, no non-manifold edges, 5,212 boundary
  edges. Some boundaries are legitimate, but substantial fragmentation and
  missing detail are unacceptable here.
- This explains why neither silhouette similarity nor a global "watertight"
  check is sufficient to judge usability.

## Decision

**Do not replace Create Mesh with this learned-stereo baseline.** Preserve it
as an instrumented comparison and a reproducible negative result. Do not hide
the loss of thin structures through indiscriminate hole filling or smoothing.

Next quality candidate: Gaussian Wrapping / surface-aware optimization using
the frozen splat's calibrated RGBs. The COLMAP export is available through
`scripts/export_splat_colmap.py`; it excludes held-out views and explicitly
labels Gaussian-center initialization as non-ground-truth.

The local Ubuntu WSL entry cannot start: its registered `ext4.vhdx` path is
missing (`Wsl/Service/CreateInstance/MountDisk/HCS/ERROR_PATH_NOT_FOUND`). No WSL
repair, distro reinstall, or system reconfiguration was attempted. A working
Linux/CUDA environment is needed to evaluate the upstream Linux/CUDA builds;
Gaussian Wrapping was **not run** and no quality claim is made for it.

Before choosing a production method, add genuinely different assets—especially
thin/open structures and solid objects—to the benchmark, and review licensing.

## Local artifacts

- `generations/stereo_ship_pilot/comparison/comparison.html` and `.png`.
- `generations/stereo_ship_pilot/comparison_384/comparison.html` and `.png`.
- Each folder contains machine-readable metrics, meshes, per-camera renders,
  and subprocess logs. Generated assets and model weights remain gitignored.
- See [reproduction commands](stereo_mesh_benchmark.md).
