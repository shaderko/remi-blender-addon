# Release wheels

Release builds fetch the unmodified PyPI wheel declared in
`blender_manifest.toml`. Binaries are ignored by Git. `wheels/lock.json` pins
the download URLs and checksums; `scripts/release.py` selects and verifies the
host platform wheel before every build.

| Package | Version | Platform | SHA-256 |
|---|---|---|---|
| PyMeshLab | 2025.7.post1 | CPython 3.13, macOS 11+, arm64 | `841b1fa15fc76ab73e594f4bbfcbe339782b36be67d9387ff5aa5f4ca422e180` |
| PyMeshLab | 2025.7.post1 | CPython 3.13, Windows x64 | `25eb2578dd6c4d1b2e0253fb3b4f8f7895e8bb4f3f1236832db0ab58e6a44998` |
| PyMeshLab | 2025.7.post1 | CPython 3.13, Linux x64, glibc 2.35+ | `99bb0f83ad20c61bdc63beb8d7f0c9ed3af6912a4770da170ce8d4bd02a7ecb0` |

Source: <https://github.com/cnr-isti-vclab/PyMeshLab/tree/v2025.7.post1>
