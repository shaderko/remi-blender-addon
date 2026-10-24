# Gaussian-to-O-Voxel: proposed learned meshing stage

## Decision

Freeze the NCM generator and learn only **Gaussian parameters → surface
geometry**, using TRELLIS.2's MIT-licensed O-Voxel representation and its
deterministic Flexible Dual Grid extractor. This avoids another image-to-3D
model and avoids rendering/depth fusion at inference.

TRELLIS.2 itself is a 4B image-to-3D generator requiring a 24GB NVIDIA GPU and
Linux. We do **not** need to run that generator. We only need:

- `o_voxel.convert.mesh_to_flexible_dual_grid` for training targets;
- `o_voxel.convert.flexible_dual_grid_to_mesh` for deterministic extraction;
- the data toolkit for asset normalization and O-Voxel precomputation.

The O-Voxel library is MIT. The conversion is CPU-capable (TRELLIS.2 reports
under 10s per mesh); CUDA is used for the fast extractor and training tooling.

## Why this is better than post-hoc meshing

Our experiments showed that TSDF, Poisson, median filtering, robust fusion, and
surfel sampling all inherit the splat's inconsistent surface. A learned model
can resolve that ambiguity using dataset priors, while the deterministic
extractor guarantees valid connectivity. The network does not generate
triangles; it predicts a surface field that O-Voxel assembles.

## Representation and targets

O-Voxel does not store the same fields as a normal SDF grid. Per active voxel it
stores:

- active voxel coordinates;
- a dual vertex position inside/around the voxel (QEF solution);
- three edge-intersection flags used to connect neighboring dual vertices.

`mesh_to_flexible_dual_grid(vertices, faces, grid_size, aabb)` returns
`voxel_indices`, `dual_vertices`, and `intersected`. The inverse function
reconstructs the mesh. This supports open surfaces, non-manifold geometry, and
thin structures such as sails and ropes.

Use a canonical asset frame of `[-0.5, 0.5]^3`; store the normalization
transform. Start at 256³. Evaluate 512³ only after 256³ is working because
active-cell memory and target storage grow quickly.

## Input representation

Each Gaussian contributes:

- position relative to the candidate cell;
- log scales;
- rotation (quaternion or 6D matrix);
- opacity;
- SH DC color (optional for geometry, useful for disambiguating surface vs
  floaters);
- anisotropy and shortest-axis normal;
- local density / neighbor count.

Do not feed all Gaussians to a global transformer. Build a sparse candidate set
from Gaussian support, then query K neighbors per candidate cell.

## Candidate cells

1. For each Gaussian, mark cells overlapping its 1.5–2σ ellipsoid.
2. Union the marks and add a one-ring expansion.
3. At 256³, this is sparse (surface-adjacent), not a dense volume.
4. During training, add hard negatives near the surface and random negatives.
5. The network predicts active/inactive; a coarse occupancy pass can propose
   cells for a second refinement pass.

This is the main efficiency lever: computation scales with surface area, not
with the full 256³ volume.

## Network

A cell-query network, not a dense sparse 3D U-Net:

```text
candidate cell x
  -> K nearest Gaussians (voxel hash / KD-tree)
  -> per-neighbor MLP (relative position, scale, rotation, opacity, normal)
  -> permutation-invariant aggregation (max + attention)
  -> 2-3 local cell-neighborhood communication layers
  -> per-cell heads
```

Suggested first configuration:

- K = 16–32 neighbors;
- width 64–96;
- 2–3 layers;
- 1–4M parameters (the 0.3–1M target may be too small for topology);
- one forward pass at inference.

Heads:

1. **Occupancy** logit.
2. **Three edge-intersection** logits.
3. **Dual vertex** offset in cell coordinates.
4. Optional **QEF inputs**: intersection points and normals on the three edges;
   solve the dual vertex analytically. This is preferable for sharp edges and
   reduces what the network must regress.
5. Optional quad-split weight.

The deterministic extractor consumes `(coords, dual_vertices, intersected)`.

### Direct O-Voxel vs learned intersections

Do not make this an either/or from the start. Use one shared encoder and ablate:

- **Baseline A:** occupancy/SDF + Marching Cubes.
- **Baseline B:** O-Voxel fields with direct dual-vertex regression.
- **Hybrid C:** O-Voxel occupancy + edge flags + learned edge intersections and
  QEF vertex solve.

Hybrid C is the recommended production candidate because it preserves O-Voxel's
topology handling while giving sharper vertices. Baseline B is easier to debug.
Baseline A only proves the data pipeline.

## Data generation

For each source GLB:

1. Normalize scale/orientation into the canonical frame.
2. Render several views with the same preprocessing/distribution used for NCM.
3. Run the frozen NCM/TripoSplat model for one or more views/seeds.
4. Filter failed or degenerate splats (silhouette, opacity, count, extreme
   floaters).
5. Convert the source mesh to O-Voxel at 256³ (and optionally 512³).
6. Align the NCM splat frame to the target frame; store the transform.
7. Store `(gaussian_attributes, ovoxel_target, source_view_camera, metadata)`.

Important distinction:

- **Original-GLB target** teaches the full object, including the unseen back.
  It is also a harder, more generative task.
- **Teacher-mesh target** from MILo/GOF/2DGS on the same splat teaches
  splat-consistent denoising. MILo is per-scene optimization and may be
  non-commercial; use it as an offline teacher/benchmark, not as the runtime
  model.
- Use both initially: original mesh for complete shape, teacher mesh for
  splat-consistent local detail. Weight or curriculum them.

Training must use actual NCM outputs, not only ideal Gaussians fitted to clean
meshes. Otherwise the decoder never sees production artifacts.

### Scale

- 100–300 assets: pipeline validity and overfit check only.
- 1k–5k: first generalization signal.
- 10k–100k: realistic target for a general model.

Estimate: NCM inference ~2s/sample; 10k samples ≈ 5–6 GPU-hours before
rendering and O-Voxel conversion. Storage is dominated by Gaussian sets and
O-Voxel targets; plan hundreds of GB at 100k, less with compression.

Local `phft/TripoSplat` has ~15 pilot assets with renders and O-Voxel-ish
outputs, plus 700 raw views, but no source GLBs in the piloted directories. It
is useful for pipeline smoke tests, not for generalization. We need a GLB source
(Objaverse-XL, Tripo's training set, or an equivalent licensed dataset).

## Losses

Start with target-field supervision:

- BCE for active cells;
- BCE for the three edge flags;
- L1 / robust loss on dual-vertex offsets, normalized by cell size;
- QEF residual if using intersections/normals;
- class-balanced sampling and hard-negative mining;
- Laplacian/edge/component regularization on the extracted mesh.

Then add rendering consistency:

- depth, normal, mask/silhouette losses against source renders;
- Chamfer/F-score only as evaluation unless a differentiable extraction path is
  available;
- a differentiable rasterizer (nvdiffrast) can fine-tune the extracted mesh,
  but it adds CUDA/Linux dependencies.

Do not make rendering loss the first milestone. Field supervision is cheaper and
easier to debug.

## Evaluation

Hold out entire assets, not views. Compare:

- our TSDF baseline;
- O-Voxel baseline A/B/C;
- MILo/GOF teacher mesh;
- original source GLB (upper bound, not the same input information);
- AnyGS2Mesh once code is available.

Metrics:

- Chamfer distance and F-score;
- normal consistency;
- edge Chamfer and open-boundary recall (critical for sails/ropes);
- hole/component count;
- silhouette/depth IoU and render depth/normal;
- texture PSNR after appearance baking;
- active cells, KNN cost, params, latency, peak memory.

Include a “thin structures” subset (rigging, sails, wires) separate from the
overall score. A model that wins Chamfer but erases ropes is not useful here.

## Runtime and WebGPU

- The cell-query MLP is portable to WebGPU.
- KNN via a voxel hash grid; avoid sparse-convolution libraries for v1.
- O-Voxel extraction is local and parallel; port it to WGSL after the Python
  reference works.
- M4 24GB can train a 1–4M MLP/PointNet, but MPS coverage and data loading may
  be limiting. The RTX 5070 is a better training target; M4 is useful for
  browser/inference work.
- TRELLIS.2 itself is not required locally; only `o_voxel` and the data
  toolkit, which may still need Linux/CUDA for building. Plan a Linux/WSL worker
  if Windows builds fail.

## Risks and open questions

1. **Target ambiguity:** the source GLB is not the “correct” mesh for a
   single-view NCM splat. The model may learn dataset priors rather than the
   input. Mitigate with teacher distillation and visible-side/back-side
   evaluation.
2. **Data scale and licensing:** Objaverse-XL and Tripo data need license review;
   MILo code may be non-commercial; TRELLIS.2 is MIT but its model materials
   have a research disclaimer.
3. **O-Voxel resolution:** 256³ may not retain ropes; 512³ is likely needed for
   high detail. Measure active cells and memory first.
4. **Small-model capacity:** 0.3–1M may underfit topology; start 1–4M and
   distill/shrink after.
5. **Extraction portability:** the reference extractor is CUDA; browser port is
   separate work.
6. **NCM domain gap:** NCM may not generalize to arbitrary rendered GLBs;
   filter and/or fine-tune the generator only if necessary.

## First milestone

1. Install/build `o_voxel` and run a mesh → O-Voxel → mesh round trip on 10
   local GLBs at 256³/512³; record active counts, timing, and thin-structure
   behavior.
2. Build 100–300 paired samples from a licensed GLB source; generate NCM
   splats from 2–4 views each; normalize and filter.
3. Implement candidate-cell generation + shared encoder + Baseline B
   (occupancy/direct dual vertex).
4. Add Hybrid C (edge flags + QEF) and compare on held-out assets against TSDF
   and MILo teacher.
5. Go/no-go: thin-structure retention, held-out Chamfer/F-score, and runtime.
   Only then scale data and add rendering fine-tuning.

## Immediate blockers

- Need a licensed GLB dataset with enough diversity (local pilot is not enough).
- Need to verify `o_voxel` builds/runs on the available Windows/RTX or Linux
  worker.
- Need to decide whether MILo/teacher use is acceptable for research only.
