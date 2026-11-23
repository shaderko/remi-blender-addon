# Shared adaptive CPU mesher

Source of truth: `remi-cloud-app/services/gaussian-to-mesh/remi_adaptive`.
`tools/dev/sync_adaptive_mesher.py <addon> [--check]` maintains the identical
Blender add-on snapshot. No source downloads, builds or pip installs occur during
reconstruction. The existing pinned `remi-splat-mesh` supplies validated types,
bounds and binary PLY I/O; its dense extraction function is not called.

Build before packaging with `python remi_adaptive/build_native.py`. CMake builds
a C++17 shared library for macOS, Windows or Linux. It uses no Python C API, so
the add-on can call the bundled library from its external Python interpreter.
The loader verifies the native ABI and compiled source SHA256. A missing or
stale library fails explicitly; it never falls back to the old dense algorithm.

The original anisotropic density, sigma floor, size normalization, radius cap,
cutoff and half-voxel convention are preserved. Gaussian k8 spacing and opacity
set the accepted threshold (reference percentile 85, power 20, floor 1e-5).
Sparse cells below level 0.03 may use twice the finest voxel width when local
shared-face/disk topology checks pass. Dense or ambiguous cells stay fine.
The coarse density grid plus lazily requested fine nodes avoid evaluating most
of the full grid. The contour is emitted directly, without decimation or caps.
Disconnected vertex fans are separated before component filtering and Taubin
smoothing. Progress callbacks delimit cancellable stages; callers can terminate
the separate worker process while a native stage is running.

Default quality recipe: 500-face minimum component, six Taubin iterations,
size normalization enabled. Dense/FAST callers select 512/256 finest resolution.
The default has no strict geometric placement-error bound. Sparse support is a
proxy for confidence; absent geometry cannot be recovered reliably, and fine
detail or openings can still change. Manifold diagnostics do not prove shape
accuracy or exclude all geometric self-intersections.

Validation on 18 real assets plus six controls: 38.4% aggregate process-CPU
reduction and 20.4% fewer triangles relative to the accepted dense quality
prototype. The 512 lantern changed 15.94→9.10 CPU seconds and 7.19M→5.24M
triangles. The controls show a small accuracy loss. Most speedup comes from
sparse/native evaluation; coarsening alone added about 6–7% on the lantern.
The local Linux amd64 image profile then fell from 15.38→12.23 reconstruction CPU
seconds (extraction 11.41→8.37, contour faces 6.16→3.09) after replacing
temporary contour vectors with fixed-capacity rings and reserving the edge set.
The 512 lantern remained byte-identical.
These are local regression measurements, not independent holdout guarantees
or Cloud hardware timings. The simpler fixed-threshold original remains faster.
