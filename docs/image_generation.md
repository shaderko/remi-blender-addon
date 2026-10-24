# Image to Gaussian splats (experimental)

Remi can run the `rand6_100_ncm` checkpoint (validation-selected step 600)
from Blender. No mesh needs to be selected. A generated splat can then be
reconstructed into a **vertex-colored polygon mesh** with **Create Mesh** and
passed directly into Optimize.

The mesher is a vendored, MIT-licensed port of Modly's `SplatToMesh` adapter:
each Gaussian is rasterized as an oriented anisotropic density kernel, an Otsu
level is extracted with Surface Nets, thin filaments are recovered, the surface
is Taubin-smoothed, and vertex colors are sampled from a co-splatted color
volume. It needs only PyTorch/scipy/NumPy/trimesh; no compiled CUDA extensions.

Both output files use Blender Z-up coordinates (the model's native frame).
Remi bypasses upstream's default viewer-oriented, Y-down PLY transform, rather
than silently rotating only the Blender display. Dedicated PLY viewers may
need their up-axis set to Z.

## Local setup

Generation runs in an external Python process. Blender does **not** load
PyTorch, install packages, download weights, or contact a cloud API. The initial
backend requires an NVIDIA CUDA GPU with bfloat16 support; CPU and Apple GPU
inference are not currently supported.

Use an external Python environment with CUDA-compatible `torch`, matching
`torchvision`, `safetensors`, `Pillow`, `numpy`, and `tqdm`. Configure that
environment's `python.exe` in the panel, not `blender.exe` or Blender's Python.
The worker uses isolated Python (`-I`), so dependencies must be installed in
that environment rather than in Python's user site-packages.

To prepare a local model bundle from the research checkout, run from the add-on
repository:

```powershell
python -B scripts/prepare_triposplat.py --source "C:\path\to\phft\TripoSplat"
```

The script only **reads** the research tree. It snapshots the three inference
source files and MIT license under `integrations/triposplat/vendor/`, and copies
the five required weight files into `models/triposplat/` (about 4.5 GB). Copies
are SHA-256 verified. Different existing files are never overwritten. Do not
point the output into the source checkout. The runtime does not reference the
research checkout after preparation.

Use `--output D:\RemiModels\triposplat` for another model location. Model weights
and generated assets are not committed or included in extension archives.
The small vendored inference sources **are** included; their exact hashes and
license are retained alongside them. Check the individual image-encoder/model
licenses before redistributing weights; this script is a local copy, not a
redistribution mechanism.

## In Blender

1. Load the updated add-on and open **3D View → N → Remi** in Object Mode.
2. Choose the **Generate** tab. **Optimize** contains the mesh workflow separately.
3. Choose the image. Transparent input is supported;
   opaque images have their background removed with BiRefNet.
4. Python is detected automatically. The **gear** opens optional settings for
   Gaussian count, seed, Python override, **Model Folder** (`models/triposplat`),
   and the output folder. These details stay out of the main view.
5. Click **Generate**. Blender stays interactive while the worker loads and
   generates. The cancel icon or `Esc` stops the process. The icon beside
   **Generate** imports an existing result; the folder icon after completion
   opens its output directory.

The workspace tabs lock to the running task during generation or a mesh
session, so progress and session-recovery controls remain visible.

### Create a mesh

After generation, the active splat shows **Create Mesh** in the Generate tab.
Click it; the external worker builds the surface and imports a new
vertex-colored mesh. The source splat is hidden but kept in the scene. The
result is associated with that specific splat, so every model in a `.blend`
file can be converted independently.

Create Mesh reads the Gaussian data from the selected object, not from the
generation folder. The original `splats.npz` may be moved or deleted after
import; the worker receives a temporary exchange archive that is removed when
the job ends. Remi switches to **Optimize** and selects the
**Reconstructed · Decimate** preset,
which reduces the surface to about 120k triangles without the destructive
initial voxel remesh. The mesh keeps its vertex colors. Run UV and Bake
manually if the reconstructed topology accepts them; the current Surface Nets
output is not always suitable for Remi's UV validator.

The gear menu contains **Mesh Detail** (Draft 160 / Standard 224 / High 288),
**Smoothing** (Crisp/Balanced/Smooth), and **Mesh Output**. Typical times on the
local RTX 5070 for 65k Gaussians are 2–5 seconds at Standard/High, plus process
startup. A 224-resolution mesh is about 1M triangles before Optimize.

This is a **lossy approximation**. It produces a clean, recognizable surface
with colors, but it cannot reproduce the full fidelity of a native Gaussian
render; fine rigging can be simplified. Use **High** and **Crisp** for more
detail, then review before accepting the Optimize result. The worker requires
`torch`, `scipy`, `numpy`, and `trimesh` in the configured external Python;
CUDA is used when available.

Start at 65,536 splats. Larger counts may exhaust GPU memory; a failed worker
leaves the scene unchanged and reports the error in its log.

The model path defaults to the development bundle when present, otherwise to
Blender's user-data folder. `REMI_TRIPOSPLAT_MODELS` can override the default.
Paths and generation settings are saved with the scene. The runtime and busy
state are not saved. Opening another `.blend` or disabling Remi stops the worker.

For installed copies, machine-local defaults can be stored in
`remi-generation.json` inside that Blender version's user **config** folder,
with string fields `python`, `models`, `output`, and `mesh_output`. This avoids changing the
user's `startup.blend` or copying model weights into the extension. Defaults
are read when Remi loads; saved scene values still take precedence, and
`REMI_TRIPOSPLAT_MODELS` overrides the configured model default. The local 5.3
installation uses this file to point at the already-prepared Remi model bundle.

Each generation is a fresh process, so it reloads the weights every time and
releases its GPU memory on exit. No warm-cache speed is promised. A local RTX
5070 smoke test produced 65,536 splats in about 30 seconds including loading.

## What appears in Blender

### Blender 5.3+ — native Gaussian rendering

When the build exposes the `GAUSSIAN_SPLAT` point-cloud type, Remi creates a
**native Gaussian point cloud**, automatically. There are no ellipsoid meshes,
Geometry Nodes modifiers, custom shaders, or third-party rendering add-ons.
Blender handles anisotropic scale, quaternion rotation, spherical-harmonic DC
color, and Gaussian opacity falloff. Use **Rendered** shading to inspect it.

All generated Gaussians are retained and rendered, including low-opacity ones.
**Preview Limit** is hidden because it only applies to the older fallback.
Existing ellipsoid objects are not silently migrated; re-import `splats.npz` in
5.3 to create a native object. The native `.blend` uses Blender's own Gaussian
data representation and remains renderable without Remi installed.

This path is tested against the installed 5.3 beta's actual API, not just a
version number. Beta behavior may change. Generation tests do not establish
5.3 compatibility for Remi's optional compiled remeshing/UV backends.

### Blender 5.1/5.2 — ellipsoid fallback

The object still stores **every** splat, even when its preview is limited. Its
base mesh contains one vertex per Gaussian and **no polygon surface**.
Non-destructive Geometry Nodes displays opaque low-poly ellipsoids, limited to
**Preview Limit** points with opacity at least 0.05. Use **Material Preview** or
rendered shading for colors. This fallback does **not** implement opacity
blending or Gaussian rasterization and is not equivalent to native rendering.

Remi's mesh-session and Full Flow entry points reject splat objects. Do not apply
the preview modifier as a substitute for reconstructing a surface: that would
only create disconnected ellipsoid proxies.

The base data is self-contained in `.blend` and retains:

| Data | Native 5.3 storage | Older fallback storage |
| --- | --- | --- |
| Z-up centers, same frame as exported PLY | `position` vector | Base vertex positions |
| Activated standard deviations | `scale` vector | `remi_splat_scale` vector |
| Unit quaternion, WXYZ | `rotation` quaternion | `remi_splat_rot_w/x/y/z` floats |
| Linear opacity | `radiance:base` fourth component | `remi_splat_opacity` float |
| Unclipped SH DC color | `radiance:base` first three components | `remi_splat_sh_dc` vector |

Native `radiance:base` is a **FLOAT4**, not an RGBA color attribute. Its RGB
components are raw SH coefficients, without pre-applied SH basis constants,
clamping, or sRGB conversion. This matches Blender's own native PLY importer.

In the fallback, `remi_splat_color`, `remi_splat_euler`, and `remi_splat_preview` are derived
display attributes. Mesh reconstruction consumes the native attributes or
archive, **not** the evaluated preview geometry.

## Saved files and recovery

Every run gets a unique directory:

```text
model.ply       native Gaussian-splat PLY, for dedicated viewers
splats.npz      versioned, pickle-free, float32 native data for Remi
input.png       background-removed, model-ready image
meta.json       checkpoint, seed, count, coordinate frame, timing
request.json    exact local inference request
status.json     worker progress or error
complete.json   immutable completion marker (written last)
worker.log      diagnostic output
```

Use the **folder icon** to inspect the result, or the **import icon** beside
**Generate** to re-import a `splats.npz`. Arbitrary third-party PLY import is not part of this
first integration. Failure and cancellation preserve logs and completed files.
An error never replaces the selected mesh. If the scene or mode changes before
generation finishes, output stays on disk and can be imported later.

Mesh conversions get a separate directory under **Mesh Output**:

```text
mesh.ply       vertex-colored reconstructed surface
report.json    source object/archive, resolution, triangle count, timing, device
request.json   exact mesher request
status.json    progress or error
complete.json  immutable completion marker
worker.log     diagnostic output
```

## Validation without the UI

```powershell
python -B -m unittest discover -s tests -p "test_generation.py"
python -B scripts/generate_splats.py "C:\path\to\image.png"
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background --factory-startup --python-exit-code 1 --python tests/blender_generation_regression.py
& "C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe" --background --factory-startup --python-exit-code 1 --python tests/blender_mesh_conversion_regression.py
```

The mesh regression uses a synthetic splat archive and a tiny PLY; it does not
need torch. To exercise the real density mesher, use the worker with a
generated `splats.npz` and the external Python.

The regression uses synthetic Gaussians and needs no model weights or torch.
For a real modal UI test (launches a temporary factory-startup Blender window,
generates, renders the available display type, saves a `.blend`, tests
cancellation, and exits):

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --factory-startup --python tests/blender_generation_modal_smoke.py -- --image "C:\path\to\image.png" --python "C:\path\to\python.exe"
```

Use the 5.3 beta executable in the same command for native generation/rendering.
To render an **existing** generation in both EEVEE and Cycles, and compare its
native attributes to Blender's own PLY importer (no inference needed):

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe" --background --factory-startup --python-exit-code 1 --python tests/blender_native_splats_smoke.py -- --archive "C:\path\to\splats.npz"
```

This writes native PNG renders, a self-contained `native_splats.blend`, and a
JSON report in a new directory beside the archive. It refuses to use an
ellipsoid fallback if native Gaussian support is missing.
