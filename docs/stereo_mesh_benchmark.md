# Learned-stereo mesh benchmark (research only)

This is a **new offline experiment**, not a replacement for Create Mesh yet.
The generator and original Gaussian archive remain frozen. No code in the
Blender UI or production density worker is changed.

The first [ship pilot results](stereo_mesh_pilot_results.md) are recorded:
the learned-stereo baseline loses more thin detail and is **not** approved as
the new production converter.

```text
Gaussian archive
  → native Blender RGB/alpha stereo pairs, known parallel cameras
  → DLNR Middlebury learned stereo in both directions
  → metric camera-Z = focal_length_px × baseline / disparity
  → alpha + subpixel left/right-consistency rejection, confidence weights
  → bounded projective TSDF + Marching Cubes, unknown-space masking
  → depth-tested vertex colors (diagnostic; UV texture baking comes later)
```

**No rendered Gaussian depth is used.** Exact camera calibration does not make
first-hit splat depth a correct surface. This tests the learned geometric prior
in [GS2Mesh (ECCV 2024)](https://gs2mesh.github.io/), not the raw-depth fusion
already tried in `fuse_splat_views.py`. This is GS2Mesh-style, not an exact
reproduction of its full implementation or reported benchmark results.

## Setup

Use external Python 3.11+ with CUDA-compatible PyTorch. Blender must expose the
native `GAUSSIAN_SPLAT` API (tested locally with Blender 5.3 beta); the opaque
ellipsoid fallback is deliberately rejected. Windows is supported without
custom CUDA extensions or Open3D's native TSDF kernels.

From the add-on repository:

```powershell
python -m pip install -r scripts/stereo_requirements.txt
python -B scripts/prepare_stereo.py
```

The second command downloads ~230 MB of official DLNR weights and a small
source snapshot into ignored `models/stereo/dlnr/`. Source is pinned to commit
`c10475fe30ff198781ecc7c712c24c781ff05096`. `provenance.json` records URLs,
sizes, and SHA-256 hashes; subsequent runs verify existing files rather than
replace them. The first checkpoint hash is recorded locally, **not** checked
against an independently published upstream digest. Inference verifies the
bundle and uses tensor-only `torch.load(weights_only=True)`.

DLNR source is MIT; its original license stays in `source/LICENSE`. Review
checkpoint/dependency licenses before product distribution. Neither downloaded
source nor weights are included in the extension. The Gaussian Wrapping,
TopoSurfel, and MILo implementations have commercial-use restrictions and are
not integrated by this prototype.

## Run one asset

Use a **new output folder** for every experiment. Existing nonempty stage
folders are rejected to prevent mixing camera poses, weights, and outputs.
The commands below are independent stages; successful render/depth stages can
be reused for multiple fresh comparison runs.

```powershell
$blender = 'C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe'
$archive = 'C:\path\to\splats.npz'
$run = 'generations/stereo_experiment_01'

& $blender --background --factory-startup --python-exit-code 1 --python scripts/render_stereo_splats.py -- --archive $archive --output "$run/views" --views 48 --evaluation-views 12 --resolution 1024

python -B scripts/infer_stereo_depth.py --views "$run/views" --output "$run/depths" --iterations 16

python -B scripts/benchmark_splat_mesh.py --views "$run/views" --depths "$run/depths" --blender $blender --output "$run/comparison" --density-resolution 224 --fusion-resolution 256
```

Defaults render a full sphere with interleaved **held-out cameras** that are
never passed to stereo or fusion. Parallel left/right cameras share the same
rotation and intrinsics; they do not toe in. Principal points use integer pixel
centers. All geometry stays in the archive's original Z-up world frame and
units. The bounds include all Gaussian centers with opacity ≥0.02, rather than
quantile-cropping legitimate thin parts; the chosen bounds policy is recorded.

Outputs:

| Path | Contents |
| --- | --- |
| `views/dataset.json` | Archive hash, camera calibration, pair/evaluation split, bounds, render settings |
| `depths/pair_*.npz` | Float32 metric depth, confidence, both positive disparity maps |
| `depths/confidence_*.png` | Rejection/coverage diagnostics |
| `depths/depths.json` | Checkpoint/source provenance, per-pair foreground retention, timing, peak PyTorch VRAM |
| `comparison/density/*.ply` | Shipped density and pre-cleanup ablation |
| `comparison/stereo/mesh.ply` | Learned-stereo surface with projected vertex colors |
| `comparison/comparison.html` | Self-contained held-out side-by-side report |
| `comparison/comparison.png` | Contact sheet |
| `comparison/comparison.json` | Per-camera metrics, means, mesh topology diagnostics |
| `comparison/*.log` | Separate subprocess logs |

`density_pre_cleanup` disables component deletion and mesh smoothing, while
retaining the same density preprocessing. Each call still runs the vendor's
adaptive iso-level selection, so this is a pipeline ablation, not necessarily
the identical extracted mesh before/after cleanup. The learned-stereo mesh has
**no** component removal, hole filling, smoothing, or decimation.

The default 256 grid uses isotropic voxels relative to the longest bounds
extent. Try `--fusion-resolution 384` in a new comparison folder if stereo
actually resolves thin details. Do not increase resolution to compensate for
invalid depths. Minimum supporting observations default to two; a one-view
mesh is diagnostic only. Unknown space is not silently capped to make the mesh
look watertight.

## Quality gate and next experiments

- Compare **missing foreground** and **extra foreground** separately, plus
  silhouette IoU, on the exact held-out cameras. These measure agreement with
  the source splat, **not ground-truth geometric accuracy**.
- Inspect sails, rigging, chair bars, and open cavities at full resolution.
  Boundary-edge/component counts are diagnostics, not a "hole score": an open
  sail is legitimate geometry. Do not reward sealing every opening.
- RGB PSNR is measured only over common foreground and cannot justify missing
  surfaces. Vertex colors are temporary; compare geometry independently before
  adding UV baking.
- Record runtime/VRAM and inspect rejected stereo pixels. Textureless surfaces,
  opacity, view-dependent effects, and thin structures may still defeat DLNR.
- Build a ten-asset benchmark with genuinely different source objects. Multiple
  ship generations/seeds do not count as independent benchmark assets.
- If stereo remains incomplete, benchmark Gaussian Wrapping on the same posed
  RGBs as an offline research candidate. Its success on captured multi-view
  scenes does not guarantee success on our single-image-generated splats.
- Do not wire a new default into Create Mesh or attempt a WebGPU port until
  geometry quality has been reviewed. Gaussian-to-O-Voxel remains a separate,
  data-dependent learned-model project, not a deterministic fix for bad depth.

## Validation

```powershell
python -B -m unittest discover -s tests -p test_stereo_reconstruction.py -v
& $blender --background --factory-startup --python-exit-code 1 --python tests/blender_stereo_camera_regression.py
```

CPU tests cover rectified camera geometry, metric disparity, invalid/occluded
predictions, subpixel consistency, unknown-space rejection, analytic-sphere
fusion and outward winding, depth-tested colors, and metric interpretation.
The Blender test independently checks the stored calibration against Blender's
actual projection API. Neither test suite downloads weights or uses the network.

## Input for a Gaussian Wrapping / surface-aware optimizer

The same training renders can be exported without COLMAP feature matching or
camera estimation:

```powershell
python -B scripts/export_splat_colmap.py --views "$run/views" --output "$run/optimization_input" --include-right
```

This writes standard `images/` and `sparse/0/{cameras,images,points3D}.txt`, plus
the original alpha `masks/` and provenance. Held-out evaluation cameras are
excluded. Sparse points are the source Gaussian centers for initialization;
they are **not** independently triangulated geometry and contain no feature
tracks. The receiving optimizer must support COLMAP text input.

This exporter does not install or launch Gaussian Wrapping. Its upstream CUDA
extensions and triangulation dependencies require a separate supported build;
do not repair/reconfigure the user's WSL installation as part of this prototype.
