# Field-aligned adaptive quad density — experimental

## Why the previous prototype was discarded

Beta.9–10 built quads from a decimated **triangle** scaffold and tiled its dual.
Measured on the user's `Untitled.glb` variant 0 (93,982 source triangles):

| | Broken triangle-dual route | Field-aligned route (new) |
|---|---:|---:|
| Quads | 9,409 | **6,472** |
| Regular (valence-4) vertices | 46.0% | **99.4%** |
| Interior poles | 5,079 | **36** |
| Aspect P95 | 5.18 | **1.15** |
| Angle error P95 | 57.8° | **8.8°** |
| Sampled max error | 0.00556 | **0.01397** (limit 0.01452) |

The triangle-dual mesh had continuous-looking shading but no usable edge flow:
every triangle boundary became a candidate quad edge, so poles and skewed quads
were unavoidable. It had **more** poles than regular vertices. That is not good
topology, even when the sampled error is low.

## What good topology means here

Research and production retopology references agree on the criteria used by the
new validation:

1. **Mostly valence-4 vertices.** Loops only flow through a regular quad grid.
   Interior valence 3/5 poles redirect loops and are acceptable in small numbers;
   valence 6+ should be avoided. Poles belong in flat, low-deformation areas,
   never on a crease or deformation line.
2. **Continuous edge loops that follow form.** Loops run around limbs, rims,
   eyes, mouths and dominant curvature. A quad soup with short, jagged rings is
   not a retopology, even if every face has four sides.
3. **Near-square, convex, planar-ish quads.** Low aspect ratio, near-90° corners,
   consistent winding, no folds or degenerate faces.
4. **Density where detail is.** Detail areas need smaller quads; flat areas do
   not. Excess density is not a quality feature.
5. **Closed/manifold connectivity, unchanged parts and boundaries.** Separate
   closed pieces stay separate; UV seams and material boundaries are not
   silently welded.

The old route failed 1, 2, 3 and 5 (it kept manifold connectivity but produced a
chaotic layout). The new route is judged with the metrics above, not face count
alone.

## How the new route works

`Surface Rebuild` (the default adaptive method, now labelled **Field-Aligned**)
does not invent topology from triangles. It uses the same native cross-field and
position-field solver as ordinary Instant Meshes:

1. Reconstruct exact GLB render seams in an **analysis copy** only.
2. Solve the field-aligned quad layout at several densities.
3. Choose the **lowest density** whose sampled deviation from the source meets
   **Surface Detail Error**. The search uses the measured error exponent and
   re-validates every sample before accepting.
4. Reject any candidate whose topology is not usable: not all quads, holes,
   non-manifold edges, extra components, wrong Euler characteristic, inconsistent
   winding, non-manifold vertex fans, less than 90% regular vertices, aspect P95
   over 2.5, or angle error P95 over 35°.
5. If no density meets the error inside **Face Limit**, the rebuild fails
   visibly. The native comparison remains visible, but **Accept is blocked** until
   the user raises the error/limit or explicitly chooses **Use Native Preview
   Instead**.

Guides are carried into the secondary field solve. `Seed Simplify` keeps the
earlier conservative quad-collapse pass. Flat/hard-planar sources may still take
the fast planar-patch path (for example a plane becomes one quad); non-planar
sources with 256+ source triangles use the field route.

### Important density limitation

The native field solver uses a **single global target edge length**. The new
route makes that global density as low as the detail budget allows, but it does
not make one region coarser than another in the same solve. On the plaque, the
flat back therefore gets roughly the same density as the detailed front. Spatially
varying density requires either a sizing field inside the solver or transition-
aware patch layout with 3–5 pole pairs; those are not implemented here. The
previous patch prototype attempted a shortcut and produced the unusable topology
shown above. This document does not claim local density contrast for the current
route.

## Controls

1. Enable **Adaptive Quads · Experimental** in **Optimize → Retopology**.
2. Choose **Field-Aligned**.
3. **Surface Detail Error** defaults to `0.01` = 1% of the object's local
   bounding-box diagonal. Lower it for more density; raise it for fewer faces.
4. **Face Limit** defaults to 25,000. The search never applies a truncated mesh;
   it fails if the limit is reached first.
5. **Time Limit** defaults to 60 seconds.
6. **Analyze Render Seams** defaults on. It only changes the analysis copy.
7. Turn Adaptive Quads off to compare with the exact native extraction.

## Actual `Untitled.glb` results — beta.11

Read-only import, Blender 5.3 Beta, error fraction `0.01`. Original SHA256:
`457aec5c8a2e08d21dedf645eadbaf044077b11e40b2af80cf909324c86ca38c`.
The 21 meshes are separate variants; they are never joined.

| Variants | Source triangles | Field quads | Regular | Poles | Aspect P95 | Angle P95 |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 93,982 | 6,472 | 99.4% | 36 | 1.15 | 8.8° || 1 / 7 | 2,686 | 2,530 | 98.0% | 52 | 1.35 | 12.4° |
| 2 / 6 | 1,506 | 7,844 | 99.4% | 48 | 1.23 | 12.4° |
| 3 / 10 / 13 | 4,052 | 5,038 | 98.1% | 96 | 1.37 | 14.3° |
| 4 / 12 | 4,944 | 8,368 | 96.7% | 277 | 1.44 | 17.0° |
| 5 | 9,924 | 4,926 | 97.3% | 135 | 1.44 | 16.7° |
| 8 | 3,858 | 2,262 | 96.1% | 88 | 1.51 | 21.2° |
| 9 / 11 | 3,973 | **rejected** | — | — | — | — |
| 14 | 9,924 | 6,984 | 97.5% | 177 | 1.42 | 15.4° |
| 15 | 18,834 | 22,716 | 98.3% | 379 | 1.33 | 12.7° |
| 16 | 1,810 | 9,460 | 99.2% | 72 | 1.23 | 10.1° |
| 17 | 3,614 | 17,488 | 99.8% | 38 | 1.12 | 5.7° |
| 18 | 3,258 | 4,144 | 99.6% | 16 | 1.12 | 7.5° |
| 19 | 3,842 | 8,384 | 99.7% | 28 | 1.12 | 6.0° |
| 20 | 93,982 | 9,356 | 99.5% | 49 | 1.14 | 7.5° |

All 19 valid variants are connected, closed, consistently wound all-quad meshes
with the source component/Euler count, no degenerate faces or non-manifold
edges, and sampled error within the configured limit. Variants **9 and 11** have
three genuinely ambiguous/non-manifold render-seam edges after exact-position
comparison; they are rejected with a clear message and are not silently repaired.

The table's variant-0 row is the intrinsic benchmark used for reproducibility.
With the app defaults (**Extrinsic Field** on), the same 1% request produced
**3,752 quads, 99.1% regular, 34 poles, aspect 1.20, 4.4° angle error** and
`0.01088` sampled error on variant 0 — the density and quality depend on the
field options, not on a hidden target count.

On variant 0, changing the error changes the density:

| Error fraction | Quads | Sampled max error | Limit |
|---|---:|---:|---:|
| 0.02 | 1,908 | 0.02736 | 0.02904 |
| 0.01 | 6,472 | 0.01397 | 0.01452 |
| 0.005 | 20,952 | 0.00670 | 0.00726 |

Counts are a result, not a target. A coarser source can need more quads than its
triangle count because quads are a different representation; the report says so
instead of claiming universal reduction.

## Reproduction

```powershell
blender --background --factory-startup --python-exit-code 1 `
  --python scripts/diagnose_surface_asset.py -- `
  --input "C:\Users\skaredko\Documents\assets\Untitled.glb" `
  --output "C:\temp\remi-diagnosis" --save-source

blender --background --factory-startup --python-exit-code 1 `
  --python scripts/benchmark_surface_asset.py -- `
  --cache "C:\temp\remi-diagnosis" --output "C:\temp\remi-field" `
  --mesh 0 1 2 15 --error 0.01
```

`scripts/compare_quad_topology.py` reports valence, aspect, scaled Jacobian and
sampled errors for any saved quad mesh. `tests/blender_surface_field_regression.py`
covers density selection, topology guards, guides and failed-accept blocking;
`tests/blender_surface_asset_runtime.py` runs the complete real-GLB workflow.

## Limits

- Finite sampling is not a Hausdorff or self-intersection certificate.
- Uniform density per solve; no local sizing field or transition templates.
- No UV/material transfer, baking, symmetry, painted importance or animation-loop
  guarantees.
- Real deformation quality still requires posing and inspecting the result.
- The planar-patch experiment remains opt-in/limited; its outputs are not used for
  non-planar production meshes.
