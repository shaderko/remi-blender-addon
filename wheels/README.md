# Release wheels

Release builds fetch the unmodified PyPI wheel declared in
`blender_manifest.toml`. The binary is ignored by Git and verified before every
build by `scripts/fetch_wheels.sh`.

| Package | Version | Platform | SHA-256 |
|---|---|---|---|
| PyMeshLab | 2025.7.post1 | CPython 3.13, macOS 11+, arm64 | `841b1fa15fc76ab73e594f4bbfcbe339782b36be67d9387ff5aa5f4ca422e180` |

Source: <https://github.com/cnr-isti-vclab/PyMeshLab/tree/v2025.7.post1>
