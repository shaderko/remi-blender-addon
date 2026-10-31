# Third-party notices

Remi is distributed under GPL-3.0-or-later. Its bundled integrations incorporate the
components below under their respective licenses. Those upstream files retain
their original copyright and license terms; this notice does not relicense
them.

The release includes the corresponding retained source code and license texts,
including the source used to build the platform-specific native binaries.

## TripoSplat inference and local NCM flow transplant

- Project: [VAST-AI-Research/TripoSplat](https://github.com/VAST-AI-Research/TripoSplat)
- License: MIT; retained at `integrations/triposplat/vendor/LICENSE`
- Snapshot: `model.py`, `triposplat.py`, and the local fork's
  `flow_transplant/faithful_model.py`; exact SHA-256 hashes are recorded in
  `integrations/triposplat/vendor/provenance.json`.

The upstream sources are retained without modification and execute only in an
external Python process. Remi's process client, Blender data adapter, preview,
and UI are separate code. Large model weights are not bundled. Local setup
copies the selected research checkpoint and frozen components; it does not
grant additional redistribution rights for image encoders or other weights.

## Spirula Studio (optional external surface tool)

- Project: [harry7557558/spirula-studio](https://github.com/harry7557558/spirula-studio)
- Supported release: `v2026.10.16`, Windows Vulkan x86_64
- License: GPL-3.0
- Corresponding source/license: <https://github.com/harry7557558/spirula-studio/tree/v2026.10.16>
- Official archive SHA-256: `9b83ba6d78a4ace562d2ad2f964d2e3eb15aaa7a78918eab54e44d8a98a649d0`
- Executable SHA-256: `eb57050c90fc046f079d2b788eb62f0cf9639afbe50941ecb4f945d52876bd14`

The executable is **not bundled or redistributed in Remi's extension archive**.
An explicit, online-access-gated installer retrieves the official GitHub release
directly and checks both hashes. The installation retains source/license links
and checksums in `provenance.json`. Remi invokes the tool as a local subprocess;
no scene images are uploaded. Rendering uses Blender; the separate Python worker
uses external numpy, scipy, Pillow, trimesh, and torch (not bundled).

## Modly SplatToMesh adapter (legacy research baseline)

- Project: [lightningpixel/modly-triposplat-extension](https://github.com/lightningpixel/modly-triposplat-extension)
- Commit: `6fabeffbaf2b3c4d43b1b4e8f94d46068e8e18a5`
- License: MIT; retained at `integrations/splat_mesh/vendor/MODLY_LICENSE.txt`
- Vendored file: `integrations/splat_mesh/vendor/splat_mesh.py`

This is no longer the Create Mesh backend. The adapter is a port of ComfyUI's native `SplatToMesh`
(`comfy_extras/nodes_gaussian_splat.py`, VAST-AI / Comfy-Org), which is
distributed under GPL-3.0. Remi is GPL-3.0-or-later, so the combination is
license-compatible. The vendored file is unmodified. Its runtime dependencies
(`torch`, `numpy`, `scipy`, `trimesh`) are external and not bundled.

## Splat texture projection

- Files: `integrations/splat_project/` and `blender/splat_projection.py`
- Runtime dependencies: `numpy`, `scipy`, and `Pillow` in the external Python
  environment configured for generation. They are external and not bundled.
- Optional CPU acceleration: `numba` (BSD-2-Clause) and its `llvmlite` dependency
  in that external environment. Neither package, LLVM runtime, nor generated
  machine-code cache is bundled. Source: <https://github.com/numba/numba>.
- Algorithm: UV-space evaluation and optional sparse-octree first-hit ray baking
  of the Gaussian field, normal estimation from
  covariance/density, confidence masking, and texture-space filling. No
  third-party rasterizer or reconstruction implementation is vendored.

## PyMeshLab and MeshLab

- Project: [cnr-isti-vclab/PyMeshLab](https://github.com/cnr-isti-vclab/PyMeshLab)
- Version: `2025.7.post1`
- macOS wheel: `pymeshlab-2025.7.post1-cp313-cp313-macosx_11_0_arm64.whl`
- macOS wheel SHA-256: `841b1fa15fc76ab73e594f4bbfcbe339782b36be67d9387ff5aa5f4ca422e180`
- Windows wheel: `pymeshlab-2025.7.post1-cp313-cp313-win_amd64.whl`
- Windows wheel SHA-256: `25eb2578dd6c4d1b2e0253fb3b4f8f7895e8bb4f3f1236832db0ab58e6a44998`
- License: GPL-3.0
- Corresponding source: <https://github.com/cnr-isti-vclab/PyMeshLab/tree/v2025.7.post1>

Remi distributes the matching unmodified platform wheel from PyPI. Blender installs it into the
extension-managed Python environment; Remi does not download or install Python
packages at runtime. The wheel includes the MeshLab filters and runtime
libraries supplied by the upstream PyMeshLab release, including the decimation
and Alpha Wrap filters used by Remi. Its license is retained inside the wheel.

## xatlas

- Project: [jpcy/xatlas](https://github.com/jpcy/xatlas)
- Revision: `f700c7790aaa030e794b52ba7791a05c085faf0c`
- License: MIT
- License text: `features/uv/engine/native/vendor/xatlas/LICENSE`

Remi vendors the upstream `xatlas.cpp` and `xatlas.h` files documented for
direct integration. The Blender-loop conversion, candidate scoring, and
pybind11 bridge are Remi code outside the vendored directory.

## Instant Meshes core

- Project: [wjakob/instant-meshes](https://github.com/wjakob/instant-meshes)
- Revision: `7b3160864a2e1025af498c84cfed91cbfb613698`
- License: BSD 3-Clause-style terms with the upstream enhancements paragraph
- License text: `features/retopology/instant_meshes/native/vendor/instant_meshes/LICENSE.txt`

Remi retains only the headless field solver, mesh extraction, topology, BVH,
serialization, and RPly-backed mesh I/O sources needed by the native module.
The standalone application, NanoGUI, GLFW, OpenGL renderer, shaders, resources,
and command-line frontend are not included. Remi's Blender session and pybind11
bridge are separate files outside the vendored source directory. The retained
`src/common.h` replaces TBB's umbrella include with the specific TBB facilities
used by the solver; the field and extraction algorithms are otherwise
unchanged.

## Eigen

- Project: Eigen 3.2.9, from the Instant Meshes/NanoGUI-pinned
  [libigl/eigen](https://github.com/libigl/eigen) fork
- Revision: `c34a9130bc585b288703bd9716d7efae194974e2`
- License: MPL-2.0 and BSD-licensed portions
- License texts: `features/retopology/instant_meshes/native/vendor/instant_meshes/ext/eigen/COPYING.MPL2`,
  `COPYING.BSD`, and `COPYING.README`

The retained dense linear algebra and geometry headers include the Apple
Silicon and x86 SSE backends from the same pinned revision. The native target defines `EIGEN_MPL2_ONLY`; a clean build fails if an
LGPL-only Eigen header is introduced. The retained Eigen source is provided
under MPL-2.0 and, as part of the GPL larger work, is additionally distributed
under GPL-3.0-or-later as permitted by MPL-2.0 section 3.3.

## Intel Threading Building Blocks

- Project: Intel Threading Building Blocks 2017.0 via
  [wjakob/tbb](https://github.com/wjakob/tbb)
- Revision: `550c18b1132ae1b06285b2488f0344617c46f0ed`
- License: Apache-2.0
- License text: `features/retopology/instant_meshes/native/vendor/instant_meshes/ext/tbb/LICENSE`

Remi retains the classic task scheduler source and its header closure, including
the Windows x64 machine headers and MASM routines, and the Linux x64
`linux_intel64.h`, `linux_common.h`, `gcc_ia32_common.h`, and `gcc_itsx.h` headers from the
same pinned revision. The standalone TBB build system, tests, allocator, allocator proxy,
documentation payload, packaging, and unused platform code are omitted. The
retained upstream TBB source files are unmodified and are built by Remi's CMake
target. The upstream snapshot contains no `NOTICE` file.

## Disjoint set

- Project: [wjakob/dset](https://github.com/wjakob/dset)
- Revision: `7967ef0e6041cd9d73b9c7f614ab8ae92e9e587a`
- License: zlib-style permissive license
- License text: `features/retopology/instant_meshes/native/vendor/instant_meshes/ext/dset/LICENSE.txt`

## Parallel stable sort

- Project: [wjakob/pss](https://github.com/wjakob/pss)
- Revision: `a91da33ea2e22f90d1babfb99c4882c485467af4`
- License: BSD 3-Clause
- License text: `features/retopology/instant_meshes/native/vendor/instant_meshes/ext/pss/LICENSE.txt`

## PCG32

- Project: [wjakob/pcg32](https://github.com/wjakob/pcg32), based on Melissa
  O'Neill's PCG random number generator
- Revision: `0ef13e68ca0be5506e1cfc0db76831e6f916e9e9`
- License: Apache-2.0
- License text: `features/retopology/instant_meshes/native/vendor/instant_meshes/ext/pcg32/LICENSE`

## RPly

- Project: RPly 1.1.3 by Diego Nehab
- License: MIT
- License text: `features/retopology/instant_meshes/native/vendor/instant_meshes/ext/rply/LICENSE`

## pybind11

- Project: [pybind/pybind11](https://github.com/pybind/pybind11)
- Version used for the bundled binary: 3.0.1
- License: BSD 3-Clause
- License text: `features/retopology/instant_meshes/native/licenses/pybind11-LICENSE.txt`

pybind11 is a build dependency rather than a vendored source tree, but its
headers are incorporated into the compiled extension, so its binary
redistribution notice is included here.
