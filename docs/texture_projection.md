# Texture projection

Remi's **Texture** workspace projects the appearance of a Gaussian splat onto
a cleaned, UV-unwrapped mesh. The primary workflow is **Project from View**:
the splats are rendered from the current viewport camera, and that view is
projected onto the visible mesh surface as a **layer**. Each view is a separate
layer that can be hidden, faded, or deleted.

The core control is a **viewport lasso**: drag a loop around the area you want
to receive the next projection. Only the screen-space area inside the loop is
used. Then project the current camera view into that area. Each view is a
separate layer that can be hidden, faded, or deleted.

This is the appearance half of the splat-to-asset pipeline: the mesh supplies
topology and UVs, while the splat supplies the look. It does **not** rely on
the splat object and mesh object being aligned in 3D; projection is matched in
screen space through the current camera.

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

The 3D bake remains available on Blender 5.1/5.2, but view projection requires
the native splat renderer.

## Workflow

1. Generate splats and run **Create Mesh**.
2. Open **Optimize**, then run Remesh / Repair / Retopology / UV as needed.
   Use **Finish** when the geometry is final.
3. Switch to **Texture**.
4. Set the viewport to the view you want to capture.
5. Click **Draw Area** and drag a loop around the region to project.
6. Click **Project from View**.
7. Move the viewport, redraw the area, and repeat for other regions.
8. Use the **Layers** panel to hide, fade, or delete individual views.
9. Optionally use **Project 3D** for an unmasked full-surface base or a normal
   map.

Projection writes each layer as an RGBA image and builds a layered material;
it does not delete the source splat or the mesh UVs. Re-project after any
geometry or UV change.

## Projection area

The area is a temporary screen-space lasso tied to the current viewport:

- **Draw Area** starts a lasso; drag a loop directly over the model.
- The overlay shows the selected region as an orange outline/fill.
- **Project from View** uses the area automatically.
- **Clear** removes the area; without an area the next view projects the full
  visible surface.
- The area is cleared after a successful projection.
- If you move the camera or resize the viewport, the old area becomes stale;
  redraw it or clear it.
- The area applies to view layers, not the optional 3D bake.

No Weight Paint mode, vertex groups, or Blender masking are involved.

## Layers

The Layers panel is ordered from bottom to top:

- **3D Base** is the optional full-surface projection.
- **View** layers are camera projections. New views are added on top.
- The eye icon hides a layer; the slider controls opacity; the X deletes it.
- The material is rebuilt from the visible layers, so hiding/deleting a layer
  does not require re-rendering any pixels.
- Images are packed by default. Disable **Pack Images** for external texture
  workflows.

A view layer contains straight alpha from the splat render, so the material
composites it smoothly over lower layers. Areas not covered by any view remain
the neutral base color until another view is added.

## Settings

| Setting | Meaning |
| --- | --- |
| Texture | Output texture resolution (1K / 2K / 4K). |
| View Alpha | Minimum rendered splat alpha included in a view layer. |
| Area Feather | Softness of the lasso edge, in viewport pixels. The default is a soft fade rather than a hard cut. |
| Smooth Mesh | Sets the target to smooth shading before projection so normal maps encode splat detail rather than facet edges. |
| Pack Images | Stores layer PNGs inside the `.blend`. |
| Samples | 3D bake only: nearest Gaussians evaluated per texel. |
| Normal Map | 3D bake only: use projected splat normals or preserve an existing baked normal map. |
| Normal source / Strength / Smoothing | 3D bake normal controls. |
| Fill Holes / Fill Background / Margin | 3D bake fill controls. |

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
7. The result is saved as an RGBA layer and added to the mesh's layer stack.

The rendered view image and the camera matrices are passed to the external
worker together, so no object-to-object 3D alignment is involved.

## 3D bake (optional)

The older **Project 3D** action samples the Gaussian field directly at every
UV texel. It provides a full-surface base layer and an optional normal map,
including a **Keep Baked** mode that preserves a normal map from **Bake →
Normal** in Optimize.

The recommended high-detail sequence is:

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
- It does not invent back-side appearance. Add more views to cover the object.
- It does not generate a normal map from a view layer yet. Use the 3D bake or a
  baked shape normal.
- Orthographic orbit views are approximated from the viewport scale; camera
  view is exact, and perspective orbit views are the primary target.
- It does not modify or replace the splat source.

## Next steps

- Patch/erase tools for cleaning individual view layers.
- Inpaint or patch a masked region from the current view using the original
  input image and splat renders as references.
- Normal-map brush/patch tools and a relighting preview.
- Shape + splat-detail + AI-detail normal compositing.

## Relation to the Gaussian mesh research

The mesh can be approximate because appearance no longer has to survive as
vertex colors. This reduces the pressure on perfect splat-to-mesh extraction:
keep the research for silhouette, thin structures, and topology, but use
projection for the asset's look.

The independent `gaussian-surface-research` project studies Gaussian normals as
hypotheses with confidence. Its confidence work is relevant, but this add-on
feature remains self-contained and does not import that project.
