# Instant Meshes guidance: analytic pilot

Local Windows x64 measurements, Blender **5.1.1**, on the guidance implementation
developed after checkpoint `76a9998`. No real-scan quality claim is implied.
Defaults: pure quads, 35° crease preservation, open-boundary alignment, extrinsic
field, deterministic hierarchy, two projection steps, automatic strength 0.65.
Curvature guidance was enabled explicitly for this benchmark (it is off in the UI
by default). Each raw candidate used a fresh solver with the same source and
requested target. All actual extracted counts are reported below.

Reproduce with `scripts/benchmark_instant_meshes.py`, described in
[the usage/implementation guide](instant_meshes_guidance.md). The script retains
raw `.npz` meshes and `report.json`; it never hides the rejected candidate behind
the selected one.

## Raw candidates, 500 requested faces

Each cell is **ordinary → guided**. Lower pole count, aspect ratio and angle
error are generally preferable, but individual metrics are not sufficient to
judge topology.

| Fixture | Actual faces | Interior poles | Aspect p95 | Angle error p95 | Measured circular cycles | Selected |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Capped cylinder | 530 → 530 | 16 → 21 | 1.569 → 1.610 | 16.77° → 17.83° | 2 → 3 | Ordinary |
| Open washer | 460 → 452 | 34 → 33 | 1.677 → 1.687 | 21.29° → 20.61° | 2 → 2 | Guided |
| Rounded hole (torus) | 524 → 508 | 44 → 36 | 1.640 → 1.561 | 23.53° → 19.03° | Not measured* | Guided |
| Sphere | 532 → 532 | 30 → 30 | 1.806 → 1.806 | 24.71° → 24.71° | Not requested | Unchanged |
| Plane | 484 → 484 | 0 → 0 | 1.008 → 1.008 | 0.27° → 0.27° | Not requested | Unchanged |

## Raw candidates, 1500 requested faces

| Fixture | Actual faces | Interior poles | Aspect p95 | Angle error p95 | Measured circular cycles | Selected |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Capped cylinder | 1634 → 1632 | 43 → 64 | 1.512 → 1.562 | 12.89° → 16.74° | 5 → 4 | Ordinary |
| Open washer | 1547 → 1581 | 62 → 60 | 1.455 → 1.439 | 14.72° → 13.74° | 2 → 2 | Guided |
| Rounded hole (torus) | 1488 → 1614 | 70 → 87 | 1.520 → 1.513 | 16.26° → 19.23° | Not measured* | Ordinary |
| Sphere | 1552 → 1552 | 40 → 40 | 1.394 → 1.394 | 14.09° → 14.09° | Not requested | Unchanged |
| Plane | 1444 → 1444 | 0 → 0 | 1.013 → 1.013 | 0.41° → 0.41° | Not requested | Unchanged |

\* The rounded-hole case receives local curvature combs, not detected circular
feature paths, so the circular-guide cycle matcher does not evaluate it.

Components, Euler characteristic, non-manifold edges and degenerate faces stayed
consistent in these runs. No raw candidate produced an extraction warning.
Negative controls received no suggestions and produced identical raw meshes.

## Geometric fidelity is a separate metric

Sampled bidirectional source/output distance p95, as a percentage of source
bounding-box diagonal:

| Fixture | 500 faces: ordinary → guided | 1500 faces: ordinary → guided |
| --- | ---: | ---: |
| Capped cylinder | 0.6414% → 0.6111% | 0.0781% → 0.0762% |
| Open washer | 0.1885% → 0.1600% | 0.0141% → 0.0156% |
| Rounded hole | 0.3887% → 0.3976% | 0.1797% → 0.1707% |

The topology guard does not use this distance in its score. A selected topology
candidate can have slightly worse geometric approximation (as in two cells above).
The finite vertex samples are not a certified Hausdorff bound; inspect silhouettes
and surfaces on production assets.

## Findings

- Closed-guide support fixes a concrete limitation: periodic samples retain their
  shape instead of being shortened/rejected by the open-stroke smoother.
- Naively making every detected circle an output-edge positional lock was much
  worse on the cylinder (an early 1500-target experiment increased poles from
  43 to 154). Automatic paths therefore default to soft orientation guidance.
- Direction hints and coarse-level attenuation reduce that overconstraint, but
  **recognition alone does not guarantee a better extraction**. The cylinder's
  ordinary extrinsic solve already follows its circular form well; the guard
  correctly retains it rather than accepting more cycles at the cost of more poles.
- The washer shows modest improvement. Curvature improves the low-budget torus
  but worsens the higher-budget one; keeping it experimental and comparing results
  is warranted. There is no universal "more guides is better" rule.
- Exact closed-loop enforcement, structured feature patches/caps and large/noisy
  scan benchmarks remain future work, not accomplishments of this pilot.

## Validation scope

The new source regression and its nine pure geometry/metric tests were exercised
in Blender 5.1 and 5.2 on Windows. Existing Instant Meshes, architecture,
session/history, automatic-flow and edit-tool regressions passed in the release
gate attempt. The complete gate is **not green**: it stops at
`tests/blender_mesh_conversion_regression.py:113`, which expects Create Mesh to
poll true in Blender 5.1 even though the generation backend now requires 5.3+.
The same failure was reproduced from checkpoint `76a9998`, independently of these
retopology changes. That unrelated generation test/backend was not altered.

The Windows archive was built and validated separately, then installed into
isolated Blender 5.1/5.2 profiles with user site-packages disabled. Installed
automatic guidance, packaged native API, Alpha Wrap, full remesh/decimate/UV/bake
flow and textured decimation smoke tests passed. Windows emitted nonfatal locked-
DLL cleanup warnings during those extension smoke runs.

Rebuilding/testing on macOS and Linux, and interactive viewport UX review, are
still required before a cross-platform release.
