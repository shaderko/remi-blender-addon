# Adaptive quads — experimental prototype

This is an **opt-in development feature**, separate from the released beta.7
board workflow. There are two adaptive methods: **Field-Aligned** re-solves the
native cross-field at the lowest density meeting the source-error budget;
**Seed Simplify** is the earlier conservative native-quad postprocess. The
triangle-dual "Surface Rebuild" prototype was removed after it produced unusable
pole/skew counts on the real asset. Neither method is a new native Instant
Meshes solver or a guarantee of animation-ready edge loops.

Field-aligned usage and measured limitations are in
[field-aligned adaptive density](surface_adaptive.md).

## Try it

1. Open a mesh card with **Edit in Blender**, then use **Optimize → Retopology**.
2. Keep **Pure Quads** enabled. The existing **Target Faces** still controls the
   ordinary Instant Meshes solve; start around 1,000–3,000 for an experiment.
3. Enable **Adaptive Quads · Experimental** and choose **Seed Simplify** to use
   the earlier postprocess described below. **Full**, **Half**, and **Quarter**
   mean fractions of the *actual native extraction*, not exact final counts.
4. Start retopology as usual. When a preview is ready, change options and click
   **Apply Adaptive Settings** to compare. Full restores the exact native mesh.
   Repeated updates always start from the native result, never from an already
   simplified mesh.
5. Inspect **Topology Diagnostics**, silhouette and the actual face count before
   accepting. The panel reports why reduction stopped. Failing to reach Quarter
   is preferable to forcing destructive collapses.
6. Accept/cancel through the existing Blender/Remi workflow. The source and scene
   objects are not modified by the preview. Apply to Card remains a separate step.

The feature defaults **off**. Options are saved in the `.blend`. Updating them
does not automatically rebuild an active preview: use Apply Adaptive Settings.
No model downloads, third-party Python dependencies, or native rebuild are needed.

## Mechanism and guards

- Rank candidate quad-diagonal collapses by local surface variation and a
  valence penalty. Merging opposite corners removes one face while keeping
  neighboring faces quads; it does not triangulate and attempt to recover quads.
- Protect open boundaries, sharp edges resolved in the native preview, and
  tubes around enabled automatic/manual guides. Sparse guides protect the
  segments between their samples too. This preserves existing supported loops;
  it cannot invent missing semantic/deformation loops.
- Check the local link condition, vertex fans, duplicate faces, winding, corner
  convexity, triangle flips, minimum corner angles, maximum edge aspect, valence,
  components, boundary edges and Euler characteristic.
- Reproject toward an orientation-compatible source sheet. Reject changed-patch
  samples outside **Shape Error**, in source-local units. The UI value is a
   fraction of the source bounding-box diagonal. The new development default is
   0.5%; previously saved values are retained. The original pilot used 0.2%.
- Retain historical reference-patch samples through every collapse. Rechecking
  only the last collapse would let small errors accumulate into large drift.
  Both possible quad diagonals are sampled and tested because Blender may choose
  either diagonal when displaying/exporting non-planar quads.
- Run three constrained relaxation sweeps to improve quad shape, accepting moves
  only when local distortion improves under the same sampled error/feature guards.
- Work cooperatively on Blender's main thread, with cancellable timer slices.
  Acceptance and conflicting edits are disabled during the pass. The prototype
  is capped at 25,000 extracted faces, 100,000 guide segments and 30 seconds.
  Unsupported input retains the ordinary preview; a failed adaptive task also
  falls back to it. Native fields are unchanged.

## What the error control does not promise

It bounds the **tested changed-patch samples**, not the Hausdorff distance of
the entire source mesh. The ordinary native result can already exceed the
requested tolerance in unchanged regions. Finite sampling can miss features
between samples. It is not a global self-intersection, silhouette-from-every-view,
UV/material seam, skinning or deformation certificate. Source noise can still
consume density. Intentional density transitions add poles and can leave worse
quad angles than a dense uniform grid; fewer faces alone is not a quality claim.

Curvature weighting inside the native solver, painted density, symmetry and
semantic joint loops are not implemented in this prototype.

## Compare, don't assume

```powershell
blender --background --factory-startup --python-exit-code 1 `
  --python scripts/benchmark_adaptive_quads.py -- `
  --output "C:\benchmark\adaptive-quads" --target-faces 1200 --error 0.002
```

The benchmark compares full/half/quarter **ordinary native solves** against
adaptive reduction from one full native extraction on a plane, a localized-detail
surface, sphere and capped cylinder. It records actual counts, poles, quad shape,
sampled bidirectional global distances, local guard statistics and timings, and
saves the meshes as NPZ files. Different actual face counts must be considered
when judging results. Analytic fixtures do not establish quality on real scans
or the user's turtle mesh; that still needs a model-specific visual comparison.

See [first pilot results and trade-offs](adaptive_quad_density_results.md).

Tests cover all-quad reduction, adaptive density distribution, unchanged inputs,
exact native restoration, repeatability, historical sample conservation, guide
segments/closed cycles, creases, thin-sheet projection, invalid topology,
failure fallback, cancellation, preview acceptance and the default-off path.
