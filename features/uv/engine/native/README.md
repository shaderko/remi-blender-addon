# Remi xatlas bridge

This extension exposes xatlas' existing-chart packing path to Blender Python.
Blender UV loops are passed as UV vertices, loop triangles as indices, and the
Remi chart identifier as xatlas' per-face material key. This preserves chart
boundaries while allowing xatlas to scale, rotate, and place the islands.
The public padding argument is the requested **gap between island edges** in
texture pixels. The initial raster pack uses half the gap per side without an
additional bilinear expansion. Remi then fits the islands continuously and
checks geometric separation, triangle overlaps, density and tile bounds.
The same native module provides BVH-based triangle and boundary queries.

Build for Blender's Python ABI. Install the resulting module into
`features/uv/engine/_native/`:

```sh
cmake -S . -B build \
  -DPython_EXECUTABLE=/opt/homebrew/bin/python3.13 \
  -Dpybind11_DIR="$(/opt/homebrew/bin/python3.13 -m pybind11 --cmakedir)" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release

# Replace the binary atomically after rebuilding. Rewriting a signed Mach-O
# binary in place can leave macOS with stale code-signature cache entries.
cp build/_remi_uv_packer.cpython-313-darwin.so ../_native/_remi_uv_packer.rebuilt.so
codesign --force --sign - ../_native/_remi_uv_packer.rebuilt.so
mv ../_native/_remi_uv_packer.rebuilt.so ../_native/_remi_uv_packer.cpython-313-darwin.so
```
