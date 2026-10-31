# Camera-aware Gaussian surfaces: ship pilot (2026-10-25)

**A useful improvement over shipped density extraction, not a production-quality
certificate.** Camera-aware opacity extraction plus explicit training-silhouette
constraints substantially improves agreement with the frozen ship. A dense
surface-optimized candidate retains more visible fine structure. Both still
need geometry review and tests on genuinely different objects before UI/default
integration.

## Inputs and methods

- Frozen 65,536-Gaussian ship:
  `generations/20261024_044333_76cc230b/{model.ply,splats.npz}`.
- Original NPZ SHA-256:
  `9cf336fd0ce572de38379ff23514a43bfa1709476c805dc2976df8f9d630cd61`.
- Original PLY SHA-256:
  `4ad6ac92e66da5780534ada6d41298d5475eb5c0687c5cfec0bba8729dbf09dc`.
  Both hashes were unchanged after the experiments.
- 72 native Blender 5.3 RGB/alpha training cameras, 1512 px, and **12 separate
  held-out cameras**. Neither held-out views nor native Blender depth supervise
  reconstruction. No DA3 supervision is used in these surface candidates.
- Pinned external Spirula v2026.10.16, Windows Vulkan. Its actual parsed camera
  matrices, intrinsics, image sizes, and world scale are checked before execution.

**Direct candidate:** camera-aware opacity occupancy, Gaussian-adaptive Delaunay
tetrahedra, marching tetrahedra, six edge-bisection steps, `carve-k=3`. Starts from
the original splats, without training.

**Dense candidate:** initialize a separate copy from the original splats and
optimize against all training RGBs/masks using the surface-oriented preset. Eight
thousand iterations produce 999,924 splats. Local training took **81.9 s**, raw
mesh extraction **37.6 s**. These are one-machine pilot timings, not guarantees.
The fixed-camera input stays in the original archive coordinate frame.

Both extractors disable edge merging, component deletion, hole filling, unseen
vertex culling, degenerate-angle repair, and quality relaxation. Upstream still
splits non-manifold vertices automatically (five splits in the raw dense result).

## Why extraction alone was not enough

The ungated direct mesh has large unsupported sheets (22.30% extra foreground).
The ungated dense mesh is much worse: **554.72% extra foreground**, despite very
good training RGB fit. Large unsupported triangles dominate its held-out renders.
The extractor's `cull-unseen=1` ablation does not fix this (544.62% extra).

The successful additional constraint is explicit, reproducible, and uses **only
training alpha masks**:

- Foreground alpha >=0.02, with two-pixel dilation to tolerate fine/subpixel edges.
- Project each vertex **and face centroid** through the exact training cameras.
- Require foreground support from >=3 cameras and allow at most 3 known-background
  contradictions across 72 cameras. Offscreen/behind-camera samples are unknown,
  not foreground or background votes.
- Reject contradicted faces; do not fill resulting openings or delete components
  by size. Keep the raw meshes and record rejection counts.

This removes **346 triangles** from the direct mesh and **298 triangles** from
the dense mesh. A tiny number of huge bad faces can matter much more than millions
of small correct ones. This is a sampled visual-hull constraint: it cannot prove
concave geometry, rule out self-intersections, or validate an entire triangle
from a finite set of samples.

## Final held-out comparison

Means over the same 12 cameras. **Reference is the source splat render, not
independently measured ground-truth geometry.**

| Method | Silhouette IoU | Missing foreground | Extra foreground | Vertices | Triangles |
| --- | ---: | ---: | ---: | ---: | ---: |
| Shipped density | 0.9657 | 0.61% | 2.93% | 492,202 | 993,108 |
| Direct opacity + silhouette constraints | **0.9887** | 0.75% | **0.39%** | 471,977 | 943,414 |
| Surface-optimized + silhouette constraints | 0.9873 | **0.58%** | 0.70% | 3,515,270 | 7,019,935 |

Common-foreground RGB PSNR: 23.57 dB density, 24.90 dB direct constrained, 24.52 dB
dense constrained. Color scores cannot compensate for missing geometry.

Clay inspection shows the new candidates preserve rigging that density extraction
melts away, and reduce the broad hull's inflated/pitted appearance. The dense
candidate retains more continuous fine structure in the inspected views. However:

- Direct constrained: **455 components**, zero boundary/non-manifold edges.
- Dense constrained: **3,021 components**, **7,597 boundary edges**, zero
  non-manifold edges. Some rigging remains fragmented; some boundaries can be
  legitimate open surfaces. Zero non-manifold edges is **not** a manifoldness
  certificate.
- The raw dense extractor reports 1,253,378 faces with angles below 5 degrees;
  removing 298 contradicted faces does not solve its sliver-triangle problem.
  This is not automatically suitable for UVs, animation, collision, or fabrication.
- The faster direct candidate scores slightly better on silhouette/color. Do not
  claim surface optimization is required for the measured agreement improvement,
  or that 7M triangles are globally better because they are more numerous.

## Artifacts

All paths below are under `generations/surface_ship_20261025/`:

- `direct_constrained/mesh.ply`: fast candidate, ~943k triangles.
- `silhouette_constrained/mesh.ply`: dense candidate, ~7.02M triangles.
- `comparison_final/comparison.html`: source + density + both candidates, full-res
  image toggles, silhouette/color metrics, hashes, and topology diagnostics.
- `comparison_final/geometry/comparison.html`: untextured clay comparison, no
  misleading RGB score.
- `comparison_final/detail_0006.png` and `detail_0011.png`: shared-crop full-object
  inspection of rigging and hull detail (source + density + both new candidates).
- `comparison/` and `comparison_visible/`: deliberately preserved negative
  extraction/visibility ablations.
- `optimizer_8k_isolated/`: exact commands, calibrated parser dump, training
  checkpoints, extraction logs, and raw mesh. Original failed launches are kept.

See [setup and commands](surface_mesh_reconstruction.md). Create Mesh, original
splats, Blender preferences, and system configuration are unchanged. Tools and
artifacts are not packaged in the extension. Next gates are multiple distinct
assets, better handling of fragments/sliver triangles, and licensing/platform
review before product integration—not hiding defects with blanket filling.
