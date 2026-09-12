# xatlas upstream

- Project: <https://github.com/jpcy/xatlas>
- Revision: `f700c7790aaa030e794b52ba7791a05c085faf0c`
- License: MIT (see `vendor/xatlas/LICENSE`)
- Vendored files: `source/xatlas/xatlas.cpp`, `source/xatlas/xatlas.h`

Only the two-file library required by xatlas' documented integration path is
vendored. The pybind11 bridge is Remi code.

Local change: `PackOptions::preserveChartShape` (off by default upstream, enabled
by the Remi bridge) skips independent X/Y expansion to integer chart extents.
Raster allocation still rounds outward. For fixed-resolution packing it also
limits the global texel scale to fit the largest chart, preserving relative
island density instead of independently shrinking an oversized chart.
