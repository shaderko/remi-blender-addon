# Posed DA3-Base ship pilot (2026-10-25)

**The first dense DA3 reconstruction fails the quality gate.** This is one
generated ship, not evidence about all objects, larger DA3 models, or all
surface-aware reconstruction methods. Create Mesh is unchanged.

## First run: 48 views, 756 px inference, 512 fusion

- Frozen source: `generations/20261024_044333_76cc230b/splats.npz`.
- Source SHA-256: `9cf336fd0ce572de38379ff23514a43bfa1709476c805dc2976df8f9d630cd61`.
- Reused the 48 native Blender 5.3 training views at 768 px and 12 separate
  held-out views. No native depth, held-out input, or camera pose estimation.
- Apache-2.0 DA3-Base checkpoint; eight overlapping context cameras per target.
  Inference took 72.5 seconds, peak PyTorch allocation 5.32 GiB on the RTX 5070.
  These figures include model loading/first-use overhead but not total driver
  memory, and are not performance guarantees.
- Both fusion variants use a 512 grid: actual shape 528 × 194 × 461, voxel size
  0.001836 in archive units, truncation four voxels, at least two TSDF observations.
- Consistency requires support from at least one other camera among 12 nearby
  cameras, relative Z tolerance 0.003 and round-trip pixel error <=1.5 px.
- **No** decimation, smoothing, component deletion, or hole filling.

Means across the 12 held-out cameras:

| Method | Silhouette IoU | Missing foreground | Extra foreground | Vertices | Triangles |
| --- | ---: | ---: | ---: | ---: | ---: |
| Shipped density (224 baseline) | 0.9659 | 0.60% | 2.91% | 492,202 | 993,108 |
| DA3 raw, 512 | 0.1140 | 88.60% | 0.0003% | 91,817 | 163,966 |
| DA3 cross-view-checked, 512 | 0.5996 | 39.61% | 0.71% | 509,414 | 884,606 |

These measure **agreement with source splat renders, not ground-truth geometric
accuracy**. Untextured clay comparisons confirm severe fragmentation, not just
bad color projection. The checked variant has **15,100 connected components**
and 103,480 boundary edges. Its zero non-manifold edge count does not make it a
usable mesh. The raw variant has 3,046 components and 12,250 boundary edges.

Checking depths helps because mutually inconsistent predictions otherwise carve
away much of the fused surface. A larger voxel/vertex budget alone is not the
missing ingredient. The checked mesh already has more vertices than the density
baseline and is much worse. Learned depths cannot be treated as observations
with guaranteed camera/world consistency merely because poses were supplied.

## Higher-detail run: 72 views, 1512 px renders, 1008 px inference

The second run keeps the source fixed, renders 72 training views plus 12
held-out views at 1512 px, and uses six-camera DA3 contexts at 1008 px. Inference
took 181.8 seconds; peak PyTorch allocation was 6.93 GiB. Fusion remains at 512.
View count, camera split, context size, and image resolution all change together;
this is a higher-detail candidate, **not an isolated resolution ablation**.

| Method | Silhouette IoU | Missing foreground | Extra foreground | Vertices | Triangles |
| --- | ---: | ---: | ---: | ---: | ---: |
| Same shipped density, new evaluation cameras | 0.9657 | 0.61% | 2.93% | 492,202 | 993,108 |
| DA3 raw | 0.0151 | 98.49% | 0.0010% | 11,905 | 22,484 |
| DA3 cross-view-checked | 0.2145 | 78.26% | 1.43% | 219,602 | 366,355 |
| Checked DA3, local surface-band fusion ablation | 0.7702 | 11.64% | 15.19% | 1,567,832 | 2,534,239 |

The local-band variant disables distant free-space carving: only depths within
the TSDF truncation band contribute. It avoids much of the destructive carving
but exposes the underlying inconsistent geometry as a noisy cloud of surfaces:
31,392 components, 594,909 boundary edges, and excessive foreground. **Even at
1.57 million vertices / 2.53 million triangles this is not a usable surface.**
Clay inspection rules it out; a higher silhouette score alone is insufficient.

This supports moving beyond image-only inferred-depth fusion for this asset.
It does not prove that a different trained model, depth refinement, or
surface-aware optimizer cannot succeed. The next experiment is
[surface-aware reoptimization](surface_mesh_reconstruction.md), not increasing
this pipeline's triangle count or hiding defects with hole filling.

## Artifacts and reproduction

See [pipeline/setup](multiview_mesh_reconstruction.md). First-run outputs:

- `generations/multiview_ship_20261025/depths/`: raw depths/scores, calibration,
  model provenance, timing, and context indices.
- `generations/multiview_ship_20261025/runtime-packages.txt`: local package versions.
- `generations/multiview_ship_20261025/comparison_512/comparison.html`: color report.
- `generations/multiview_ship_20261025/comparison_512/geometry/comparison.html`:
  untextured geometry report, with no misleading RGB similarity score.
- Per-variant PLYs, coverage/geometry diagnostics, and separate subprocess logs.

All artifacts and weights remain ignored, not included in the extension.

Higher-detail outputs use the same structure under
`generations/multiview_ship_hires_20261025/`, including the three fusion variants,
the held-out color/clay reports, and recorded runtime package versions.
