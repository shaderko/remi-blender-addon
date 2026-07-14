# Third-party notices

Remi is distributed under GPL-3.0-or-later. The native Interactive Instant
Meshes module also incorporates the components below under their respective
licenses. Those upstream files retain their original copyright and license
terms; this notice does not relicense them.

The release includes the corresponding retained source code and license texts,
including the source used to build the bundled macOS Apple Silicon binary.

## Instant Meshes core

- Project: [wjakob/instant-meshes](https://github.com/wjakob/instant-meshes)
- Revision: `7b3160864a2e1025af498c84cfed91cbfb613698`
- License: BSD 3-Clause-style terms with the upstream enhancements paragraph
- License text: `instant_meshes/native/vendor/instant_meshes/LICENSE.txt`

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
- License texts: `instant_meshes/native/vendor/instant_meshes/ext/eigen/COPYING.MPL2`,
  `COPYING.BSD`, and `COPYING.README`

Only the compiler-verified dense linear algebra and geometry include closure is
retained. The native target defines `EIGEN_MPL2_ONLY`; a clean build fails if an
LGPL-only Eigen header is introduced. The retained Eigen source is provided
under MPL-2.0 and, as part of the GPL larger work, is additionally distributed
under GPL-3.0-or-later as permitted by MPL-2.0 section 3.3.

## Intel Threading Building Blocks

- Project: Intel Threading Building Blocks 2017.0 via
  [wjakob/tbb](https://github.com/wjakob/tbb)
- Revision: `550c18b1132ae1b06285b2488f0344617c46f0ed`
- License: Apache-2.0
- License text: `instant_meshes/native/vendor/instant_meshes/ext/tbb/LICENSE`

Remi retains the classic task scheduler source and its compiler-verified header
closure. The standalone TBB build system, tests, allocator, allocator proxy,
documentation payload, packaging, and unused platform code are omitted. The
retained upstream TBB source files are unmodified and are built by Remi's CMake
target. The upstream snapshot contains no `NOTICE` file.

## Disjoint set

- Project: [wjakob/dset](https://github.com/wjakob/dset)
- Revision: `7967ef0e6041cd9d73b9c7f614ab8ae92e9e587a`
- License: zlib-style permissive license
- License text: `instant_meshes/native/vendor/instant_meshes/ext/dset/LICENSE.txt`

## Parallel stable sort

- Project: [wjakob/pss](https://github.com/wjakob/pss)
- Revision: `a91da33ea2e22f90d1babfb99c4882c485467af4`
- License: BSD 3-Clause
- License text: `instant_meshes/native/vendor/instant_meshes/ext/pss/LICENSE.txt`

## PCG32

- Project: [wjakob/pcg32](https://github.com/wjakob/pcg32), based on Melissa
  O'Neill's PCG random number generator
- Revision: `0ef13e68ca0be5506e1cfc0db76831e6f916e9e9`
- License: Apache-2.0
- License text: `instant_meshes/native/vendor/instant_meshes/ext/pcg32/LICENSE`

## RPly

- Project: RPly 1.1.3 by Diego Nehab
- License: MIT
- License text: `instant_meshes/native/vendor/instant_meshes/ext/rply/LICENSE`

## pybind11

- Project: [pybind/pybind11](https://github.com/pybind/pybind11)
- Version used for the bundled binary: 3.0.1
- License: BSD 3-Clause
- License text: `instant_meshes/native/licenses/pybind11-LICENSE.txt`

pybind11 is a build dependency rather than a vendored source tree, but its
headers are incorporated into the compiled extension, so its binary
redistribution notice is included here.
