# Texture projection

Remi's **Texture** workspace projects the appearance of a Gaussian splat onto
a cleaned, UV-unwrapped mesh. With **no drawn area**, **Bake Whole Mesh · Octree**
matches a local Gaussian layer around every UV texel and creates color, tangent-space
normal, and confidence maps. With a drawn area, **Project from View** projects
that viewport render as a separate layer. View layers can be hidden, faded, or deleted.

The core control is a **viewport lasso**: drag a loop around the area you want
to receive the next projection. Only the screen-space area inside the loop is
used. Then project the current camera view into that area. Each view is a
separate layer that can be hidden, faded, or deleted.

This is the appearance half of the splat-to-asset pipeline: the mesh supplies
topology and UVs, while the splat supplies the look. It does **not** rely on
the splat object and mesh object being aligned in 3D for **view layers**, which
match in screen space. **Whole-mesh ray baking does require world-space alignment**
between source and target, as in the Generate → Create Mesh → Optimize workflow.

## Why view projection

The reconstructed mesh carries vertex colors, but they are tied to mesh
vertices and are lost or smeared by remeshing and decimation. A 2K texture has
roughly four million samples. More importantly, the splat remains the original
appearance source even after the mesh topology changes.

Projecting from the viewport camera is predictable:

- What you see in the viewport is what gets projected.
- The mesh and splat do not need matching object origins.
- Each view becomes a layer you can review in isolation.
- A bad view can be deleted without rebuilding the other views.

## Requirements

- Blender 5.3+ for **Project from View**, because it uses native Gaussian-splat
  rendering.
- A mesh in Object Mode with a UV map, or let projection generate Remi UVs.
- A Remi Gaussian splat linked to the mesh (Create Mesh stores the link), or a
  splat chosen manually in **Splat Source**.
- An external Python environment configured in **Generate → settings** with
  `numpy`, `scipy`, and `Pillow`. The existing image-generation environment
  normally already has these.
- **Numba** in that same external Python enables the compiled parallel CPU
  octree tracer. It is optional, is not loaded into Blender, and is never
  automatically downloaded/installed. Without it (or if compilation fails),
  the worker explicitly reports the slower NumPy fallback in progress/logs.

The 3D bake remains available on Blender 5.1/5.2, but view projection requires
the native splat renderer.

## Workflow

1. Generate splats and run **Create Mesh**.
2. Open **Optimize**, then run Remesh / Repair / Retopology / UV as needed.
   Use **Finish** when the geometry is final.
3. Switch to **Texture**.
4. Keep the low-poly mesh active and leave the projection area clear. Click
   **Bake Whole Mesh · Octree** for a full-surface base color/normal bake.
   This does not run the reconstruction mesher or change the target's vertex count.
5. Inspect **Confidence**. Check source alignment before adjusting **Maximum Source
   Gap** or **Layer Width**. Increasing them too far can match a different part.
   Filling missing colors does not turn those texels into source evidence.
6. Optionally add view-specific touch-ups:
   set the viewport, click **Draw Area**, drag a loop, then **Project from View**.
   Move the viewport and repeat; use **Layers** to hide, fade, or delete views.

Projection writes each layer as an RGBA image and builds a layered material;
it does not delete the source splat or the mesh UVs. Re-project after any
geometry or UV change.

## Projection area

The area is a temporary screen-space lasso tied to the current viewport:

- **Draw Area** starts a lasso; drag a loop directly over the model.
- The overlay shows the selected region as an orange outline/fill.
- **Project from View** uses the area automatically.
- **Clear** removes the area and enables **Bake Whole Mesh · Octree**.
- The explicit **Project from View** button still projects the whole visible
  view when no area is drawn; it is not silently changed into a different bake.
- The area is cleared after a successful projection.
- If you move the camera or resize the viewport, the old area becomes stale;
  redraw it or clear it.
- Whole-mesh octree baking is disabled while an area is drawn. Clear it first,
  or use Project from View for just the selected area.

No Weight Paint mode, vertex groups, or Blender masking are involved.

## Layers

The Layers panel is ordered from bottom to top:

- **3D Base** is the full-surface octree or legacy field bake.
- **View** layers are camera projections. New views are added on top.
- The eye icon hides a layer; the slider controls opacity; the X deletes it.
- The material is rebuilt from the visible layers, so hiding/deleting a layer
  does not require re-rendering any pixels.
- Images are packed by default. Disable **Pack Images** for external texture
   workflows.
- Each bake loads fresh image datablocks. Earlier packed/painted images are kept,
  not reloaded into the new bake. This avoids Blender reusing old packed pixels
  even when an image's filepath points at a new PNG. Scenes baked with beta.4 or
  earlier need a fresh bake; upgrading alone does not replace their stored images.

A view layer contains straight alpha from the splat render, so the material
composites it smoothly over lower layers. Areas not covered by any view remain
the neutral base color until another view is added.

When **Normal Map** is enabled, each view layer also stores a tangent-space
normal map derived from a normal pass of the same rendered Gaussians. The
normal pass uses the same visibility, overlap, lasso, and camera as the color
pass. Visible normal layers are composited in the material over an existing or
3D-projected base normal.

## Settings

| Setting | Meaning |
| --- | --- |
| Texture | Output texture resolution (1K / 2K / 4K). |
| View Alpha | Minimum rendered splat alpha included in a view layer. |
| Area Feather | Softness of the lasso edge, in viewport pixels. The default is a soft fade rather than a hard cut. |
| Normal Map | Render a normal pass from the visible Gaussians and store it with each view layer. |
| Normal Strength | Blend between the clean mesh normal and the rendered Gaussian normal. |
| Normal Smoothing | Texture-space smoothing radius for rendered Gaussian normals, in pixels. |
| Smooth Mesh | Sets the target to smooth shading before projection; the local-layer bake also uses clean area-weighted geometric normals for search directions rather than split shading normals. Authored custom normals are not deleted. |
| Pack Images | Stores layer PNGs inside the `.blend`. |
| Whole Mesh Method | Octree · Local Layers (default), Density Surface (Legacy), or Surface Sampling (Legacy). |
| Maximum Source Gap | Local layers: largest allowed target-to-source-layer distance, as a percentage of source support-bounds diagonal; default 2%. Does not authorize painting from arbitrarily distant foreground parts. |
| Layer Width | Local layers: depth bandwidth around the matched layer, default 0.5% of source support-bounds diagonal. Contributions more than twice this distance from the anchor are excluded. |
| Ray Distance | Legacy density only: search distance on **each side** of the target; default 10% of the Gaussian support-bounds diagonal. |
| Surface Density | Legacy density only: first-hit summed-density threshold, default 0.5; not native renderer alpha. |
| Trace Steps | Legacy density only: maximum adaptive steps per ray; default 512. Not used by the local-layer compositor. |
| Samples | Legacy field method only: nearest Gaussians evaluated per texel. Octree does not truncate the field to the nearest N centers. |
| Normal Map (3D bake) | 3D bake only: use projected splat normals or preserve an existing baked normal map. |
| Normals / Strength / Depth Neighborhood | Local layers: conservative source-depth plane fits, or Clean Geometry Only. Uncertain depth falls back to clean geometric normals. Strength blends the result with the target shading frame. Legacy density retains its density-gradient normal. |
| Fill Holes / Fill Background / Margin | Local layers fill at most two pixels inside the same UV island, retaining zero confidence; large missing areas stay neutral. Background/margin padding is outside the mesh UV coverage. |

## How view projection works

1. The actual viewport is rendered with only the splat visible, using the
   viewport's own view and projection. No approximate temporary camera is used.
2. Two passes over black and white viewport backgrounds recover straight
   splat color and alpha; the result has the same framing as the viewport.
3. The worker rasterizes the mesh UVs to world positions and projects them
   through the viewport's exact projection matrix.
4. The target mesh is rasterized into a depth buffer, so only the front-most
   surface is painted; back surfaces and hidden faces are rejected.
5. Each visible texel samples the rendered splat view, including its alpha.
6. The lasso polygon is rasterized at the view resolution, feathered, and
   multiplied into the rendered splat alpha.
7. When Normal Map is enabled, a temporary copy of the splats is rendered with
   each Gaussian's shortest covariance axis encoded as color. Because the
   native splat renderer supplies the alpha, overlap, and visibility, this is a
   normal pass of the same rendered surface. It is projected with the same
   depth test and lasso, then encoded in the mesh tangent basis.
8. The color and normal results are saved as a view layer and added to the
   mesh's layer stack.

The rendered view image and the camera matrices are passed to the external
worker together, so no object-to-object 3D alignment is involved.

## Whole-mesh octree local-layer bake

The target needs UVs, not a high-poly reconstructed source normal map:

```text
Generate Gaussians → Create Mesh → Optimize → Finish
  → Texture: clear area → Bake Whole Mesh · Octree
```

1. Export the linked Gaussian object's current data and target mesh into the
   same world frame. Original generation archives can be deleted. The current
   source transform must be uniform-scale; apply/resolve a non-uniform source
   scale before using this bake. The target can have a non-uniform/mirrored transform.
2. Build a sparse octree of **anisotropic Gaussian supports**, bounded at 3.5 sigma.
   Each node bounds the complete ellipsoids, not just centers; long/thin Gaussians
   are not lost at cell boundaries. This hierarchy skips empty space; it does not
   create a quantized voxel mesh or dense bounding-box volume.
3. Rasterize each UV texel to a target position, shading frame, and clean
   geometric search direction. Find the peak of each intersecting anisotropic
   Gaussian along that local ray. Anchor to the closest usable contribution,
   rejecting it if it exceeds **Maximum Source Gap**. The search can start inside
   the cloud; it is not an exterior density-shell first-hit test.
4. Alpha-composite candidates front-to-back only within the matched layer's
   depth bandwidth. A separate foreground layer outside that bandwidth cannot
   cover the target's layer. There is no fixed nearest-neighbor candidate cap.
   Color stays in the model/native RGB convention, not a recovered PBR albedo.
   This local 3D compositor is an approximation, not Blender's screen-space 3DGS
   renderer or an exact multiview visibility solution.
5. Estimate source detail from chart-local plane fits of composited source-depth
   positions. Confidence, planarity, neighborhood support, and agreement with
   clean geometry gate and attenuate those estimates. Uncertain regions use
   clean geometric normals; **Clean Geometry Only** disables estimated detail.
   No noisy Gaussian density-gradient normals are used by this method.
6. Encode the normal in Blender's exported per-corner tangent basis (including
   mirrored handedness), not an unrelated per-triangle basis. Load normals as
   **Non-Color** and combine them with the full-mesh base and any view layers.
7. Save color, normal, confidence, and a report with bounds/node counts, matched
   texels, misses, rejected gaps, weak opacity, and source-normal coverage. Cancel stops the external
   worker and removes its temporary exchanges; partial outputs remain for diagnosis.

### Parallel tracing and speed

With Numba available, the worker processes **32,768-ray batches** in compiled
machine code on up to **eight CPU threads**, sharing one read-only flattened
octree. Each thread owns its scratch buffers; worst-case scratch is bounded to
128 MiB across the tracer. This is a CPU implementation, not a GPU mode.
The local compositor uses float64 arithmetic without fast-math or a
nearest-neighbor cap. First use can spend a few seconds compiling; later runs
reuse the machine-local cache. No compiled Numba cache is bundled in the ZIP.

The progress counter counts **UV rays**, not Gaussian candidates or march iterations.
Progress shows rays/sec and the
active CPU thread count (or NumPy fallback); the report records the backend.

On the saved character diagnostic (262,144 Gaussians, 8,103 triangles, 659,969
UV rays at 1K), the local-layer worker completed in **7.5 seconds wall time**,
with 655,760 matched texels and 4,209 unmatched. The former density bake had
587,596 hits and 72,373 misses. These are correspondence/coverage diagnostics,
not a guarantee that every matched texel is visually correct. Four matched
unlit views also reduced color error against native renders; relit materials
still differ from the unlit Gaussian appearance.

### Legacy density surface

Choose **Density Surface (Legacy)** to reproduce the former first-density-hit
method. It retains adaptive density intervals, ten bisections, and analytic
density-gradient normals, with **Ray Distance**, **Surface Density**, and
**Trace Steps** controls. Its normals can be noisy and its first hit can be the
wrong nearby part; it is not the recommended appearance-transfer method.

On the earlier captured slow-bake input (262,144 Gaussians, 27,495 triangles,
593,983 UV rays at 1K, 128 Trace Steps), the parallel worker completed all maps
in **17.9 seconds wall time**. This is one-machine/input timing, not a guarantee.
A comparison sample had identical hit, cage-clipping, and step-limit flags;
floating-point map values are not promised bitwise identical. To reproduce a
reference/compiled comparison without modifying the original job:

```powershell
python -B scripts/benchmark_octree_tracer.py --request path/to/frozen/request.json --output generations/my_speed_comparison
```

Both exchange archives referenced by the request must still exist. The benchmark
uses a fresh output directory and preserves the source mesh/Gaussians.

A legacy density ray starting inside the field at the cage boundary is **not** treated
as a valid source surface. Increase Ray Distance in that case. A no-hit texel
retains zero confidence; optional color filling uses nearby color and a neutral
target normal, not invented Gaussian detail.

The legacy method is an **implicit-density surface bake**, not native 3DGS alpha compositing
or a PBR decomposition. Its threshold is an explicit surface hypothesis. The
octree accelerates sampling but cannot make fuzzy/volumetric Gaussians into
ground-truth normals. A 200-vertex target can receive dense shading detail, but
textures cannot restore rigging, silhouette, or geometry removed by simplification.

## Legacy field bake

Choose **Whole Mesh Method → Surface Sampling (Legacy)** for the older action.
It samples the Gaussian field directly at every
UV texel. It provides a full-surface base layer and an optional 3D normal map,
including a **Keep Baked** mode that preserves a normal map from **Bake →
Normal** in Optimize.

For an independently baked high-poly normal, the legacy sequence remains:

```text
Create Mesh (reconstructed high-poly)
  → Optimize: Remesh / Repair / Retopology / UV
  → Bake → Normal (source checkpoint = reconstructed high-poly)
  → Finish
  → Texture: Project from View layers
  → Optional: Project 3D with Normal Map = Keep Baked
```

If the session starts from an already-cleaned mesh rather than the
reconstructed mesh, the Bake Normal source checkpoint has no more detail than
the target. In that case, use the projected splat normal or start the cleanup
session from the reconstructed mesh.

## What this is not

- It is not a true PBR material decomposition. Standard 3DGS has no albedo,
  roughness, metallic, or AO channels. Roughness/metallic must be authored or
  estimated separately; AO can be baked from the cleaned mesh.
- It can transfer a source layer that exists behind another part, but cannot
  invent hidden/back-side appearance absent from the Gaussians. More views can
  expose existing data; they cannot recover data that was never captured.
- Gaussian normals are estimates, not stored ground truth. The view normal pass uses
  each Gaussian's shortest covariance axis, so thin/surfel-like splats give the
  most reliable result; fuzzy or volumetric clusters can produce noisy normals.
- View normals are view-dependent. A normal layer is correct for the view it was
  captured from; capture additional views for other sides.
- It does not modify or replace the splat source.

## Next steps

- Patch/erase tools for cleaning individual view layers.
- Inpaint or patch a masked region from the current view using the original
  input image and splat renders as references.
- Normal-map brush/patch tools and a relighting preview.
- Depth-gradient normal refinement for fuzzy splat clusters.
- Shape + splat-detail + AI-detail normal compositing.

## Relation to the Gaussian mesh research

The mesh can be approximate because appearance no longer has to survive as
vertex colors. This reduces the pressure on perfect splat-to-mesh extraction:
keep the research for silhouette, thin structures, and topology, but use
projection for the asset's look.

The independent `gaussian-surface-research` project studies Gaussian normals as
hypotheses with confidence. Its confidence work is relevant, but this add-on
feature remains self-contained and does not import that project.
