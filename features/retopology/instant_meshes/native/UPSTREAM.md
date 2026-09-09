# Vendored Instant Meshes core

The files under `vendor/instant_meshes` are a curated, headless subset of
[`wjakob/instant-meshes`](https://github.com/wjakob/instant-meshes) at commit
`7b3160864a2e1025af498c84cfed91cbfb613698`.

The standalone NanoGUI/GLFW/OpenGL application, shaders, resources, and batch
entry point are intentionally excluded. Remi adds `InteractiveSession` and
Pybind11 bindings without changing the field optimization or mesh extraction
algorithms. See `vendor/instant_meshes/LICENSE.txt` for the upstream BSD-style
license and attribution requirements.

The dependency trees are curated too. Eigen and TBB contain only the
compiler-verified Apple Silicon include/source closure used by this module;
legacy build systems, tests, allocators, unused numerical modules, and unused
platform backends are excluded. The only retained Instant Meshes source edit is
that `src/common.h` names the TBB facilities the solver uses instead of loading
TBB's all-in-one header; this does not change the algorithm. Exact dependency
revisions, pruning notes, and all redistribution notices are recorded in the
repository-level `THIRD_PARTY_NOTICES.md`.
