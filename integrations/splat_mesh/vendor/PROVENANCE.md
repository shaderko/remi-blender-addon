# Vendored Gaussian mesher

`splat_mesh.py` is copied without modification from:

- Repository: <https://github.com/lightningpixel/modly-triposplat-extension>
- Commit: `6fabeffbaf2b3c4d43b1b4e8f94d46068e8e18a5`
- File: `splat_mesh.py`
- License: MIT (see `MODLY_LICENSE.txt`)

The upstream file states that it is ported from ComfyUI's native
`SplatToMesh` (`comfy_extras/nodes_gaussian_splat.py`, VAST-AI / Comfy-Org).
ComfyUI is distributed under GPL-3.0. Remi is GPL-3.0-or-later, so this
combination is license-compatible; the upstream notices are retained here.

Runtime dependencies (`torch`, `numpy`, `scipy`, `trimesh`) are installed in the
external Python environment and are not bundled with Remi.
