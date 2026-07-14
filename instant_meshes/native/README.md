# Remi Instant Meshes native core

This directory builds the headless, interactive native field solver used by
Remi. It intentionally contains no standalone application, NanoGUI, GLFW,
OpenGL code, or command-line frontend.

## macOS Apple Silicon build

The build needs a CPython 3.13 development installation because Blender ships
the runtime but not Python headers. Build with a matching Homebrew Python:

```bash
MACOSX_DEPLOYMENT_TARGET=11.0 \
  /opt/homebrew/bin/python3.13 -m pip wheel . --no-deps --wheel-dir dist
```

The resulting `cp313`/`arm64` module is ABI-compatible with Blender 5.1's
CPython 3.13 runtime. The release module is copied into
`instant_meshes/_native/` so end users do not need a compiler, CMake, Homebrew,
or internet access.

## Scope

`session.cpp` adds a persistent orientation/position field session around the
vendored Instant Meshes algorithms. `bindings.cpp` exposes direct NumPy array
input, surface guide constraints, asynchronous field solves, visualization
snapshots, singularities, and direct quad extraction.

See [UPSTREAM.md](UPSTREAM.md), the vendored licenses, and the repository-level
`THIRD_PARTY_NOTICES.md` for exact provenance and redistribution terms.
