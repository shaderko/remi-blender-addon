# 3DGS to mesh: research and experiment log

> **Update:** the shipped path is now the Modly/ComfyUI `SplatToMesh` density +
> Surface Nets mesher, vendored under `integrations/splat_mesh/vendor/`. It
> produces a much cleaner result than the TSDF experiments below and is the
> current **Create Mesh** implementation. The experiments remain documented as
> the reason depth/TSDF fusion was rejected.

## Goal

Turn a Remi image-generated Gaussian splat into a mesh detailed enough to run
the Optimize workflow, ideally approaching the visual quality of the splat.

## New experiment: learned stereo (2026-10-24)

The offline prototype in [stereo_mesh_benchmark.md](stereo_mesh_benchmark.md)
tests RGB stereo depth estimation, not the native splat-depth fusion below.
It compares shipped density, density without component cleanup/mesh smoothing,
and learned-stereo fusion from identical held-out cameras. Create Mesh is
unchanged until this experiment demonstrates a quality improvement.

## Why post-hoc extraction is not enough

The Remi checkpoint is a **feed-forward single-image** model. Its Gaussians are
optimized for novel-view appearance, not for surface alignment. The same object
region can contain overlapping, semi-transparent, differently scaled primitives
that render well but do not agree on one depth. A mesh extractor therefore sees
a noisy union of surfaces rather than a clean shell.

Native Blender 5.3 rendering provides useful RGB, alpha, and depth passes, but
the depth values are already the noisy first-hit surface of those primitives.
Fusing more native-depth views reduces random error but cannot remove systematic
inconsistency. This was a limitation of the methods tested below, not proof that
post-hoc methods with learned geometric priors cannot improve the surface.

## Experiments (pirate-ship checkpoint, 65,536 Gaussians)

All experiments used 48 posed EEVEE views at 512 px unless noted. Camera poses
and intrinsics were known exactly; no learned stereo or monocular depth model
was used. Comparison is against the native splat render.

| Method | Resolution / input | Geometry result | Main failure |
| --- | --- | --- | --- |
| TSDF + marching cubes | 128³ | smooth, plausible silhouette | ropes and sail detail lost |
| TSDF + marching cubes | 160³, narrow truncation | more detail | hull holes, noisy surface |
| TSDF + marching cubes | 256³ | visible ropes and rigging | high-frequency speckles; 1.9M triangles |
| TSDF + 3×3 depth median | 256³ | much cleaner hull | fine detail softened; still not splat-level |
| TSDF + outlier-only median | 256³ | retains more detail | residual speckles |
| TSDF + two-pass consensus | 256³ | little improvement | depth disagreement is systematic, not random |
| Open3D Poisson on fused depth | 50k points | smooth but blobby | loses thin structures |
| MeshLab screened Poisson on fused depth | 50k points | very noisy/rocky | point normals and positions inconsistent |
| Flattened-Gaussian surfel sampling + Poisson | 542k points | large sail blobs | heuristic flattening does not produce a surface |
| MeshLab decimation (200k) | from 256³ | retains silhouette | does not remove geometric speckles |

Native gaussian rendering remains visibly cleaner and more detailed than every
mesh result. Vertex colors alone are also insufficient: they are limited by mesh
vertex density and inherit visibility/depth noise. A high-resolution texture
projection would improve appearance but would not recover missing geometry.

## Published methods and fit

| Method | What it adds | Input / cost | Fit for Remi now |
| --- | --- | --- | --- |
| **SuGaR** (CVPR 2024) | Encourages flat, surface-aligned Gaussians, then Poisson extraction | Multi-view images, per-scene optimization; non-commercial license | High quality potential; would require re-optimizing Remi splats |
| **2D Gaussian Splatting** (SIGGRAPH 2024) | 2D oriented disks with depth-distortion and normal losses; TSDF extraction | Posed images, CUDA rasterizer, optimization | Strong surface prior; same re-optimization requirement |
| **Gaussian Surfels** (SIGGRAPH 2024) | Flattened Gaussians, normal-depth consistency, screened Poisson | Posed images, optimization, monocular normals | Similar; better thin-surface behavior |
| **Gaussian Opacity Fields** (SIGGRAPH Asia 2024) | Ray-traced opacity level set, adaptive marching tetrahedra | Trained 3DGS, optimization; non-commercial | Directly extracts a surface, but still needs surface regularization for a clean result |
| **PGSR** (TVCG 2024) | Unbiased planar depth/normal rendering | Posed images, optimization | High-quality planar surfaces; training required |
| **MILo** (SIGGRAPH Asia 2025) | Differentiable mesh-in-the-loop extraction; mesh and Gaussians co-optimized | Posed images, CUDA, training | Closest to a clean detailed mesh, but a substantial engineering port |
| **TopoSurfel** (2026) | Mesh-guided surfel evolution, density control, hole filling | Posed images, optimization | Addresses floaters/holes directly |
| **MAtCha** (CVPR 2025) | Atlas of charts, monocular depth, 2DGS, adaptive mesh extraction | A few posed images, MASt3R/DepthAnything, CUDA; mixed licenses | Strong sparse-view geometry, but reconstructs from input images again |
| **MeshSplat** (AAAI 2026) | Feed-forward 2DGS from 1–2 posed images | Trained network, CUDA rasterizer | Generalizable, but trained for indoor scenes; quality on object splats unverified |
| **GS2Mesh** (ECCV 2024) | Renders stereo pairs, learned stereo depth, TSDF | Existing 3DGS + pretrained stereo model | Worth testing: exact poses/native splat depth are not ground-truth surface geometry; learned stereo provides a distinct geometric prior |
| **Confidence-based extraction** (2026) | Learns confidence to balance photometric/geometric supervision | Per-scene training | Could suppress view-dependent artifacts, but still training-based |

## KIRI 3DGStoMesh 2.0

The linked page is a showcase, not an open implementation. KIRI's public API
accepts images/video for 3DGS scanning and can return a mesh; it does not expose
a documented endpoint that accepts an existing Gaussian PLY. Its Blender add-on
contains an older interactive rough-mesh converter, not the 2.0 reconstruction
engine. Exact reproduction is not possible from public material.

## Viable directions

### A. Mesh-native image-to-3D (recommended for a usable asset)

Use a mesh generator (TRELLIS-image, Hunyuan3D, InstantMesh, or TripoSR) on the
same input image, then use Remi's Optimize flow. The splat can remain as the
appearance/reference source for texture transfer.

- Pros: clean topology, proper UVs/textures, much closer to a production asset.
- Cons: not literally 3DGS-to-mesh; shape may differ from the splat; additional
  model weights and runtime.
- Effort: medium, depending on the chosen model's Windows/CUDA support.

### B. Surface-aware 3DGS reconstruction (closest to the requested result)

Port or integrate a 2DGS/SuGaR/MILo-style optimization, initialized from the
Remi splats and supervised by renders from many virtual cameras. This is the
only path that can plausibly approach KIRI-level geometry while staying derived
from the splats.

- Pros: preserves the 3DGS source, can produce detailed surfaces.
- Cons: CUDA extensions, per-scene optimization, license constraints,
  research-grade integration; not a quick UI feature.
- Effort: high.

### C. External mesh service/API

Use KIRI 3DGS Scan with mesh output or another commercial converter. This
requires uploading images/video or a supported splat format; credentials and
cost apply. It is the fastest route to verified quality but not self-contained.

### D. Ship best-effort TSDF as experimental

Not recommended. The 256³ result is not competitive with the splat and would
misrepresent the feature's quality.

## Current code status

The production path is now **Create Mesh**, using the vendored Modly/ComfyUI
density + Surface Nets mesher under `integrations/splat_mesh/`. It reads the
selected object's Gaussian data, writes only an ephemeral exchange archive, and
associates each result with its source object, so multiple models in one file
work independently. The `Reconstructed · Decimate` preset is selected
automatically after import.
Surface Nets topology is not always UV-friendly, so UV/Bake remain manual for
now. The prototype scripts under `scripts/` are retained as research tools and
are not packaged. `phft` remains read-only.
