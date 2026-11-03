# Automatic Instant Meshes guides

Remi can recognize geometric features and suggest surface paths before solving
the Instant Meshes field. This is **opt-in**, inside the existing interactive
retopology workspace; it does not replace the external AutoRemesher action or
change the default full-flow recipe.

## Use

1. In **Optimize → Retopology**, enable **Automatic Feature Guides** before
   **Start Interactive Retopology**.
2. Leave **Circular Rims**, **Cylindrical Flow**, and **Keep Better Preview** on.
   Enable **Curvature Flow** separately to experiment on other rounded surfaces.
3. Review lime-colored suggestions. Toggle individual suggestions, or remove
   them all, without removing manually drawn guides. **Apply / Regenerate
   Guides** applies changed detection/strength settings. **Reset Disabled
   Suggestions** restores suggestions you previously disabled.
4. Use the existing orientation/output-edge tools for corrections. **Draw Closed
   Edge Loop** draws an explicitly periodic edge guide: finish near its start.
5. Inspect **Topology Diagnostics** and accept/cancel through the normal Remi
   session. Detection and previewing do not add mesh objects or write history.

Suggestions are runtime data, not saved editable Blender curves. Disabled
suggestions survive solver-density rebuilds within the workspace, but not closing
the workspace/reopening a saved file. An automatic guide is editable by disabling
it and drawing a manual replacement; there are no control-point drag handles yet.

## Recognition

- **Circular rims:** trace only unambiguous closed, degree-two boundary or sharp
  crease components. Fit a plane and circle, reject incomplete arcs, backtracking,
  ellipses, excessive fitting error and features below the requested resolution.
  The fitted circle classifies the path; the guide follows the original source
  polyline, not an invented perfectly circular replacement surface.
- **Cylinders:** segment smooth connected patches at sharp edges. Require a
  reliable axis from the face-normal distribution, circular radial support, full
  angular coverage and sufficient height. One or three circumferential sections
  suggest both circular and perpendicular axial quad flow. Cones, partial tubes,
  and unreliable/noisy fits are not guessed into cylinders.
- **Curvature:** fit a symmetric local shape operator to one-ring normal
  differences. Add spaced, short orientation combs only where anisotropy and fit
  consistency are high. Flat regions and spherical/umbilic regions have no
  preferred direction and receive no arbitrary combs. This is a local geometric
  hint, not semantic anatomy/deformation-loop recognition.

Analysis uses NumPy already bundled with Blender. It makes no downloads and uses
no additional native dependency. Work is bounded to 500k source triangles, 300k
vertices, a user-selectable 1–256 suggestions (default 64), and 8192 samples per
path. Above the analysis limit it reports a note and runs the ordinary solver.
These limits do not limit manual Instant Meshes source meshes.

## Constraints and closed loops

`features/retopology/instant_meshes/native/session.cpp` owns the additions around
the unmodified vendored solver:

- A guide has an explicit `closed` flag. Duplicating its first point at the end
  is accepted too. Periodic tangents include the seam and do not call the upstream
  shortest-path smoother, which would shorten circular paths and reject matching
  endpoint triangles. Existing open manual strokes keep the upstream smoother.
- Closed/automatic paths are surface-sampled without shortening. On hierarchy
  rebuild, hit triangle IDs are recomputed rather than reused. Available normals
  reject projection onto incompatible nearby sheets.
- Automatic directions are averaged in rotational-symmetry space rather than
  overwritten by the last sample. Their confidence/strength remains below 1 and
  decays at coarse levels. Explicit boundary/manual constraints take priority.
- Detected guides default to **orientation hints**. Arbitrary positional locks
  around several circular sections can conflict with lattice spacing and create
  worse topology. Manual **Output Edge** and **Closed Edge Loop** guides still
  constrain edge positions as well as directions.

This encourages circular edge flow. It **does not guarantee exact closed output
loops** or their vertex counts. Exact feature-loop enforcement, controlled patch
layouts/caps, symmetry, and semantic/deformation topology require further work
beyond this field-guidance layer.

## Keep Better Preview

With enabled suggestions and no manual guides, this option runs ordinary and
guided candidates from the same field seed at the same requested face target.
It stores only the baseline field arrays, snapshots and preview, not a second
native hierarchy or scene mesh. The selected result retains matching native
fields, so updating/extracting the preview does not silently revert the choice.
Failure of the guided pass recovers the completed ordinary preview.

The policy rejects additional degenerate/non-manifold faces/edges, components,
openings in closed surfaces, changed Euler characteristic, excessive quad
stretch/angle distortion, increased pole density, or lost measured circular
cycles. Otherwise, a combined distortion/pole/guide-following score must improve
by at least 1%. The panel explains the selection. Manual guides bypass this
comparison so a requested correction is never silently overruled. Disable the
option to inspect the raw automatic result.

This is a **heuristic guard**, not a guarantee of validity or visual superiority.
It does not check self-intersections, semantic edge-loop placement, deformation
quality or every possible geometric error. It costs two field solves/extractions,
and forces preview generation even if automatic preview updates are disabled.
Existing interactive extraction warnings remain advisory; acceptance is still
the user's decision.

## Measurements and reproduction

`quality.py` measures components, Euler characteristic, boundary/non-manifold
edges, degeneracy, quad fraction, irregular valence, aspect ratio and corner-angle
error. Interior regular valence is four; boundary regular valence is three, so
intentional boundary corners count as irregular in that summary.

Guide metrics consider nearby tangent-aligned output edges, not simply edges
crossing a path. Circular closure requires a degree-two output-edge cycle inside
a tube of 0.35 median output-edge lengths, winding once around the fitted axis.
This conservative test can miss a visually adequate but irregular loop; coverage
alone is not evidence of a closed loop.

Run in factory-startup/background Blender:

```powershell
blender --background --factory-startup --python-exit-code 1 `
  --python scripts/benchmark_instant_meshes.py -- `
  --output "C:\benchmark\instant-meshes" --target-faces 1500
```

The benchmark records **both raw candidates**, the selection decision, timing,
guide-following, mesh quality and sampled bidirectional source/output distance in
`report.json`, plus `.npz` meshes for inspection. Actual face counts are reported;
equal requested targets do not imply exactly equal extracted counts. Fixtures
include a capped cylinder, open washer, torus-like rounded hole, and negative
controls (plane and sphere). These analytic fixtures are not a real-scan quality
claim. See [pilot measurements](instant_meshes_guidance_results.md).

The release gate includes `tests/blender_instant_meshes_guides_regression.py`:
shape preservation, seam acceptance, soft coarse-level weights, invalid input,
manual priority, density reprojecting, persistent disabled suggestions, ordinary
field restoration and guided-failure recovery. It also runs the pure detector
and metric tests using Blender's NumPy. Existing Instant Meshes tests continue
to cover transaction acceptance, cancellation/Back and warning paths.

## Building

The binding changed; rebuild native modules before running this source checkout
or building a package (`python3.13 scripts/release.py native`). An old bundled
module is rejected with a clear error rather than silently ignoring new guides.
Windows x64 can be built/tested locally on Windows. macOS/Linux modules must be
rebuilt on their corresponding release hosts; a Windows test does not validate
those binaries. No vendored Instant Meshes algorithm source is changed.
