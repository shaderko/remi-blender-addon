# Image to Gaussian splats (experimental)

Remi can run the `rand6_100_ncm` checkpoint (published default: step 1500)
from Blender. No mesh needs to be selected. A generated splat can then be
reconstructed into a **vertex-colored polygon mesh** with **Create Mesh** and
passed directly into Optimize.

Create Mesh uses the external **remi-splat-mesh** backend to extract an opacity
iso-surface from the anisotropic Gaussian field. **Dense** uses a 512³ field;
**Fast** uses 256³. Both retain vertex colors without automatic simplification.
The older silhouette/Spirula pipeline is no longer used.

Both output files use Blender Z-up coordinates (the model's native frame).
Remi bypasses upstream's default viewer-oriented, Y-down PLY transform, rather
than silently rotating only the Blender display. Dedicated PLY viewers may
need their up-axis set to Z.

## Local setup

Generation runs in an external Python process. Blender does **not** load
PyTorch, install packages, or contact an inference API. Optional model downloads
use a separate subprocess only after explicit confirmation. The initial
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

### Download weights from Hetzner (installed add-ons)

You do not need the research checkout when a model release has been published.
In the **Remi Board settings gear**, the **Model Download URL** defaults to the public release
`https://remipublic.hel1.your-objectstorage.com/models/triposplat-rand6_100_ncm-step1500-af36bcb6-v1/blender/`.
Choose a writable **Model Folder** and click
**Download Models**. Enable **Allow Online Access** in Blender Preferences first.
Confirm the approximately 4.5 GB download; Blender remains interactive. The
cancel button or `Esc` stops it. Verified completed files are reused on retry;
cancelled/incomplete files restart, and conflicting existing weights are never replaced.
Transient interruptions during an active transfer resume the current file with
a checked HTTP range instead of discarding the downloaded prefix.
Downloads are streamed, size/SHA-256 verified and atomically installed. Images
are never uploaded, no S3 credentials belong in the add-on, and the ZIP remains
weight-free. Model-download logs appear through the folder icon.

The URL may also be configured with `REMI_TRIPOSPLAT_DOWNLOAD_URL` or a
`download_url` string in the per-profile `remi-generation.json`. Use a trusted,
versioned release: the HTTPS manifest supplies the expected checksums. The
website uses a separate `/webgpu/` export in the **same bucket**; PyTorch weights
are not interchangeable with ONNX graphs. Blender generation still requires
CUDA/PyTorch, not WebGPU. Downloads require only the external Python standard
library. No network access is needed during generation once files are installed.

Bucket setup and publishing live in the website repository's
`tools/model-storage/README.md`. Public model storage must be separate from
private user uploads, and redistribution rights must be checked before upload.

### Generate locally

1. Load the updated add-on and click **Remi Board** in the 3D Viewport header.
2. Add an image to the board. The old sidebar Generate tab is removed;
   **Optimize** and **Texture** remain native mesh tools.
3. Select the image card. Transparent input is supported;
   opaque images have their background removed with BiRefNet.
4. Python is detected automatically. The **gear** opens optional settings for
   Gaussian count, seed, Python override, **Model Folder** (`models/triposplat`),
   model-cache idle time, and the output folder. These details stay out of the
   main view.
5. Click **Generate 3D**. Blender stays interactive while the worker loads and
   generates a separate 3D card. Use its **Cancel** action to stop the job.

Progress stays on the board even while another Blender workspace is active.
Native Remi editing sessions keep their existing recovery controls.

### Create a mesh

This beta targets **Blender 5.3 on Windows x64**. Install the reconstruction
backend into the configured external Python environment, not Blender's Python:

```powershell
python -m pip install -r integrations/splat_mesh/requirements-splatmesh.txt
```

Use the requirements file inside the installed extension or the source checkout.
It pins `remi-splat-mesh` and installs its NumPy/SciPy/scikit-image dependencies.
The backend can run on CPU; compatible CUDA/PyTorch accelerates field building.
No Spirula executable, Surface Tool Folder, or Install Surface Tool action is
needed. Existing generation models are separate from this meshing backend.

After generation, select the 3D card and click **Create Mesh**. The external
worker creates a separate vertex-colored mesh card; neither asset is imported
into the working scene automatically. Every 3D card can be converted independently.

Board jobs read the card's file-backed Gaussian archive, so keep the generation
folders and `splats.npz`. Use **Edit in Blender** to inspect the mesh in its own
ordinary Blender scene, or **Add to Scene** to place it in your project.
No Optimize session or reduction starts automatically. **Apply to Card** sends
manual mesh changes back to that same card while retaining previous files.
Run UV and Bake manually if the reconstructed topology accepts them; fragments,
sliver triangles, and open/non-manifold regions can still need cleanup.

For a source-faithful look, finish the geometry in **Optimize**, then switch to
**Texture**. The Texture workspace projects the linked splat's base color and
surface-derived normal onto the cleaned mesh, with a confidence map for holes.
It is re-runnable after remesh/UV changes and does not depend on the mesh's
vertex colors. See [texture_projection.md](texture_projection.md).

Right-click **Create Mesh** in the fixed toolbar to set **Mesh Quality**, **Mesh
Output**, and the external **Python**. Left-click runs those saved defaults:

- **Dense** (default): 512³ extraction, retaining more surface detail at greater
  memory cost.
- **Fast**: 256³ extraction for a quicker, coarser result.

The backend cleans sub-threshold components and smooths the extracted surface;
component removal and topology statistics are reported. This is not automatic
polygon-budget reduction or deformation-ready retopology.

Remi releases its own warm generation worker before reconstruction to free GPU
memory. Blender stays interactive. **Cancel Job** stops the selected job.
Progress, `mesh.ply`, and `report.json` remain in the unique Mesh Output job
directory. Failure/cancellation never replaces the source card or scene object.

This remains a **lossy reconstruction**, not ground-truth or guaranteed hidden
geometry. Inspect fine rigging, disconnected fragments, and topology before
reducing the mesh. See the [backend release notes](releases/2.7.0-beta.6.md).
The older [surface reconstruction details](surface_mesh_reconstruction.md) and
[pilot comparisons](surface_mesh_pilot_results.md) describe the superseded pipeline.

Start at 65,536 splats. Larger counts may exhaust GPU memory; a failed worker
leaves the scene unchanged and reports the error in its log.

The model path defaults to the development bundle when present, otherwise to
Blender's user-data folder. `REMI_TRIPOSPLAT_MODELS` can override the default.
Paths and generation settings are saved with the scene. The runtime and busy
state are not saved. Disabling Remi stops the warm worker; opening another
`.blend` stops only an active generation and leaves an idle warm worker
available until its cache timeout.

For installed copies, machine-local defaults can be stored in
`remi-generation.json` inside that Blender version's user **config** folder,
with string fields `python`, `models`, `output`, `mesh_output`, and `download_url`. This avoids changing the
user's `startup.blend` or copying model weights into the extension. Defaults
are read when Remi loads; saved scene values still take precedence, and
`REMI_TRIPOSPLAT_MODELS` overrides the configured model default. The local 5.3
installation uses this file to point at the already-prepared Remi model bundle.
Saved scene settings take precedence over defaults.

By default the external worker keeps the checkpoint, image encoders, and
decoder loaded for **5 minutes** after a generation. Repeated generations in
that window skip the weight-loading step; the next request reuses the same
process. The **Keep Model Loaded (min)** control in the gear changes this
timeout. Set it to **0** to always use a fresh process and release GPU memory
immediately.

The warm worker still writes one request/output directory per generation. Only
its model state is shared. Cancellation or disabling Remi stops the process and
releases GPU memory; an idle worker exits automatically after the configured
timeout. The persistent worker log lives beside the cache under Blender's
temporary directory, and each output's `worker.log` points to it.

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
worker.log      diagnostic output (points to the warm server log when cached)
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
# Warm-cache protocol test is included; set Keep Model Loaded to 0 in Blender
# to force the one-shot path.
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
