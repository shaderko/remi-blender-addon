# Geometry-first Gaussian reconstruction: posed DA3 experiment

This is an **offline research path**, not a new Create Mesh default. A high
vertex count is allowed; it is not evidence of accurate geometry. All source
Gaussians stay frozen. No native Gaussian Z pass enters reconstruction.

See [recorded pilot results](multiview_mesh_pilot_results.md). The first ship
run is **not** a production-quality improvement.

## Pipeline

1. Render native Gaussian RGB and alpha with calibrated cameras, including
   separate held-out cameras. Reuse `render_stereo_splats.py`: DA3 consumes the
   left training cameras, not the stereo disparities or held-out images.
2. Run **DA3-Base** with exact world-to-camera extrinsics and intrinsics. Each
   target is inferred jointly with its nearest training cameras. Overlapping
   contexts bound VRAM use; they do **not** guarantee global depth agreement.
3. Align outputs to the supplied camera/world scale using the official API.
   Save raw predictions as well as foreground-masked depths, processed
   intrinsics, RGB, and soft fusion weights. Alpha threshold is 0.2; no fixed
   confidence percentile is discarded, which could erase thin structures.
4. Compare raw depths with a cross-view-checked variant. Support requires depth
   agreement and round-trip pixel reprojection; occluded/invalid samples cannot
   support a surface. Record per-view rejection instead of concealing holes.
5. Fuse at **512** grid resolution by default. Extract actual indexed triangles,
   not a point-cloud GLB. Keep unknown space masked. Apply **no decimation,
   smoothing, component deletion, or hole filling**.
6. Compare against shipped density on the same held-out cameras, with both
   vertex-colored and **untextured clay** views. Clay shows geometric defects
   that color can disguise. Vertex colors are diagnostic, not final texturing.

## Setup (Windows, existing CUDA Python)

The runtime is outside Blender. An isolated virtual environment avoids changing
the existing generation environment. `--system-site-packages` can reuse an
already installed CUDA torch/torchvision pair; otherwise install that pair in
the new environment using the appropriate official PyTorch instructions first.

```powershell
python -m venv --system-site-packages models/multiview/runtime
$python = 'models/multiview/runtime/Scripts/python.exe'
& $python -m pip install -r scripts/multiview_requirements.txt
python -B scripts/prepare_multiview.py
```

The prepared model is about **542 MB**. The source is pinned to
`3d835ec1a5802d64a8b8b15f817a1ab54809bfe4`; DA3-Base weights/config to
`f4a6c9b3c95e41c82048423d3493a81ec3fa810e`. The safetensors digest is verified
against the upstream Hugging Face LFS SHA-256. Source/config hashes are recorded
from pinned HTTPS downloads and checked before executing source. Downloads do
not execute source or install packages. Existing/modified bundles are not
silently overwritten.

DA3-Base's model card and upstream source use Apache-2.0; retain upstream notices
and review all dependencies before distribution. This path deliberately does
not download the non-commercial Large/Giant weights. The pinned model uses
PyTorch scaled-dot-product attention: no xformers/custom CUDA build or Gaussian
renderer extension is needed for depth inference. The upstream API imports
several export dependencies even when export is disabled; the requirements
include them. Neither the runtime nor model bundle is packaged with the add-on.

## Run

Use a **new output folder** for every stage; existing nonempty folders are
rejected. Source archives, prior outputs, Blender preferences, and the production
converter are unchanged.

```powershell
$blender = 'C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe'
$python = 'models/multiview/runtime/Scripts/python.exe'
$archive = 'C:\path\to\splats.npz'
$run = 'generations/multiview_experiment_01'

& $blender --background --factory-startup --python-exit-code 1 --python scripts/render_stereo_splats.py -- --archive $archive --output "$run/views" --views 72 --evaluation-views 12 --resolution 1512 --training-sides left

& $python -B scripts/infer_multiview_depth.py --views "$run/views" --output "$run/depths" --resolution 1008 --context-views 8

# Obtain the unchanged density baseline using mesh_density_baselines.py, or
# point --density-mesh to an existing baseline from this exact source archive.
& $python -B scripts/mesh_density_baselines.py --archive $archive --output "$run/density" --resolution 224

& $python -B scripts/benchmark_multiview_mesh.py --views "$run/views" --depths "$run/depths" --density-mesh "$run/density/density_shipped.ply" --blender $blender --output "$run/comparison" --resolution 512
```

The 1008 px setting above is a quality experiment, **not** a tested VRAM promise.
Start with 756 px and eight context views on a 12 GB GPU. These resolutions are
multiples of the network's 14 px patch size. RGB, alpha, and principal points are
resized together before inference, avoiding a half-pixel calibration shift.
Do not upsample a depth map and call the result additional geometric evidence.

The recorded 1008 px run used **six** context views (6.93 GiB peak PyTorch
allocation on the local 12 GB RTX 5070). This does not qualify the eight-view
1008 px combination or other GPUs.

To isolate fusion settings without rerunning the model:

```powershell
& $python -B scripts/fuse_multiview_depth.py --views "$run/views" --depths "$run/depths" --output "$run/fusion_alternative" --resolution 512 --relative-tolerance 0.002 --min-support 2 --truncation-voxels 3
```

Outputs include `depths/depths.json` (model/source provenance, timings, VRAM,
context camera indices), raw depth/confidence NPZs, `mesh.ply`, per-view retention,
topology diagnostics, held-out `comparison.html`, and
`geometry/comparison.html` for untextured inspection. A raw-depth ablation is
explicitly labeled; it is not automatically an acceptable reconstruction.

`benchmark_multiview_mesh.py --surface-band-ablation` adds a diagnostic that
integrates only the local truncation band around consistent depths, rather than
letting distant predicted depths carve free space. It may preserve more coverage
while retaining doubled/internal surfaces; it is **not** an automatic repair or
the default fusion policy.

## Acceptance criteria

- Inspect the actual untextured surface, not just color resemblance.
- Preserve thin/open structures without replacing them with inflated tubes or
  sealing cavities. Boundary edges are not automatically defects; components
  and non-manifold edges are diagnostics, not a complete validity certificate.
- Track missing and extra foreground separately. Held-out render agreement is
  **not ground-truth geometric accuracy**.
- Raising grid resolution cannot repair invented/inconsistent depths. DA3 adds
  a learned prior, not independent observations of the original object.
- Test multiple distinct objects before production integration. If depth fusion
  still fails, the next substantive step is surface-aware optimization against
  the posed RGBs/masks, not another density-resolution knob or blanket smoothing.

## CPU validation

```powershell
python -B -m unittest discover -s tests -p test_multiview_reconstruction.py -v
python -B -m unittest discover -s tests -p test_stereo_reconstruction.py -v
```

These tests establish calibration/filter mechanics and dataset separation;
they do not establish learned-model quality on generated assets.
