# Remi xatlas bridge

This extension exposes xatlas' existing-chart packing path to Blender Python.
Blender UV loops are passed as UV vertices, loop triangles as indices, and the
Remi chart identifier as xatlas' per-face material key. This preserves chart
boundaries while allowing xatlas to scale, rotate, and place the islands using
pixel-exact padding.

Build for Blender's Python ABI:

```sh
cmake -S . -B build \
  -DPython_EXECUTABLE=/opt/homebrew/bin/python3.13 \
  -Dpybind11_DIR="$(/opt/homebrew/bin/python3.13 -m pybind11 --cmakedir)" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```
