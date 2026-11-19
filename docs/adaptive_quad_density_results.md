# Adaptive quad prototype: first pilot

Blender 5.3 beta, deterministic native solves at a requested 1,200-face base
budget; adaptive sampled patch tolerance 0.2% of source-local diagonal. These
are analytic fixtures, **not** measurements of the user's turtle or real scans.
The benchmark includes both possible quad triangulations and constrained
post-collapse relaxation.

| Fixture | Actual full native | Native half target | Adaptive half target | Adaptive quarter target |
|---|---:|---:|---:|---:|
| Plane | 1,156 | 576 | 578 | 515 |
| Localized detail | 1,183 | 580 | 591 | 560 |
| Sphere | 1,210 | 604 | 821 | 821 |
| Capped cylinder | 1,288 | 600 | 871 | 871 |

The adaptive pass reached half on the plane/detail fixture. It stopped early
on the sphere and cylinder, and did not reach quarter on any fixture: shape,
feature, topology and quad-quality guards took priority over the face budget.
All returned adaptive meshes retained quads and the initial component count,
Euler characteristic and boundary-edge count, without additional non-manifold
edges or degenerate faces.

## The important trade-off

At the half-budget setting, sampled source-to-output 95th-percentile distances
and quad quality were:

| Fixture | Native / adaptive distance (local units) | Native / adaptive aspect P95 | Native / adaptive interior poles |
|---|---:|---:|---:|
| Plane | 0.000383 / 0.000146 | 1.01 / 2.74 | 0 / 52 |
| Localized detail | 0.007542 / 0.005878 | 1.28 / 2.41 | 4 / 146 |
| Sphere | 0.007139 / 0.005488 | 1.63 / 1.86 | 40 / 243 |
| Capped cylinder | 0.017257 / 0.006721 | 1.70 / 2.12 | 26 / 245 |

These are **not equal actual face counts**, especially on the sphere/cylinder.
The distance comparison cannot establish an equal-budget win. More importantly,
the prototype adds many poles and increases quad stretching despite relaxation.
It demonstrates adaptive allocation and preservation guards, not finished
production retopology. Keep it opt-in; do not replace the native default.

A separate controlled grid test had 400 initial quads and retained substantially
more faces on the curved half than the flat half after coarsening. Protected
guide segments, a pre-existing circular guide cycle, sharp creases and exact
boundary vertices survived the dedicated regressions. Previewing, cancellation,
failure fallback, native restoration and accepting/Back were also tested.

## Next evaluation

Use the actual turtle with matched final face counts, compare shell silhouette,
neck/leg deformation and pole placement. The next algorithmic work should
reduce unnecessary poles through patch-level coarsening or stronger topology
optimization; lowering the triangle count alone is not an acceptable success
criterion. A density brush/native spatial metric is still future work.

Reproduce with [the benchmark command](adaptive_quad_density.md#compare-dont-assume).
