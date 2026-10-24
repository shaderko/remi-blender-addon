# Splat appearance projection

Remi's **Texture** workspace projects the appearance and surface detail of a
Gaussian splat onto a cleaned, UV-unwrapped mesh. It is the appearance half of
the splat-to-asset pipeline: the mesh supplies topology and UVs, while the
splat supplies the look.

This is intentionally separate from **Create Mesh**. Create Mesh produces an
approximate surface from the splats; Remi's repair, remesh, retopology, and UV
tools then make that surface usable. Projection runs **after** that cleanup.

## Why project instead of relying on vertex colors

The reconstructed mesh carries vertex colors, but they are tied to mesh
vertices and are lost or smeared by remeshing and decimation. A 2K texture has
roughly four million samples; a one-million-triangle mesh has one million
vertex samples. More importantly, the splat remains the original appearance
source even after the mesh topology changes.

Projection writes:

- **Albedo/base color** — projected SH DC color. This is an appearance color,
  not a physically de-lit albedo; it contains the lighting present in the
  splat.
- **Tangent-space normal** — derived from the splat field and expressed in the
  cleaned mesh's UV tangent basis.
- **Confidence** — per-texel coverage. Low-confidence texels are visible in the
  Texture workspace and are the targets for future repair/inpainting tools.

## Requirements

- Blender 5.1+ with Remi installed.
- A mesh in Object Mode with a UV map, or let projection generate Remi UVs.
- A Remi Gaussian splat linked to the mesh (Create Mesh stores the link), or a
  splat chosen manually in **Splat Source**. Object translation, rotation, and
  uniform scale are applied automatically; apply a non-uniform splat scale
  before projecting.
- An external Python environment configured in **Generate → settings** with
  `numpy`, `scipy`, and `Pillow`. The existing image-generation environment
  normally already has these.

The projector runs in that external process. Blender stays interactive, and a
failed or cancelled run never replaces the mesh material.

## Workflow

1. Generate splats and run **Create Mesh**.
2. Open **Optimize**, then run Remesh / Repair / Retopology / UV as needed.
   Use **Finish** when the geometry is final.
3. Switch to **Texture**.
4. Confirm the linked **Splat Source**, texture size, and normal settings.
5. Click **Project Splats**. The first run may generate Remi UVs.
6. Inspect the **Confidence** view. Black/low-confidence areas are holes or
   regions where the splat has no reliable evidence.
7. Re-project after any geometry or UV change. The splat source remains
   available, so this is non-destructive to the original asset.

If you already ran **Bake → Normal** in Optimize, choose **Normal Map → Keep
Baked** in the Texture workspace. Projection then keeps that shape normal map
and replaces only the base color (and writes the projected normal/confidence
PNGs for later comparison). This is the practical shape-normal + projected-color
combination.

The recommended high-detail sequence is:

```text
Create Mesh (reconstructed high-poly)
  → Optimize: Remesh / Repair / Retopology / UV
  → Bake → Normal (source checkpoint = reconstructed high-poly)
  → Finish
  → Texture: Project Splats with Normal Map = Keep Baked
```

If the session starts from an already-cleaned mesh rather than the
reconstructed mesh, the Bake Normal source checkpoint has no more detail than
the target. In that case, use the projected splat normal or start the cleanup
session from the reconstructed mesh.

Projection writes a new material and packed images; it does not delete the
source splat. Use **Texture** / **Confidence** to switch views and **Open
Projection Folder** to inspect the raw PNGs and report. For local fixes, use
Blender's Texture Paint mode on the projected albedo image; the material uses a
standard image texture and UV map.

## Settings

| Setting | Meaning |
| --- | --- |
| Texture | Output resolution (1K / 2K / 4K). Start at 2K; 4K is memory-heavy for large meshes. |
| Samples | Nearest Gaussians evaluated per texel. 16 is a good default; more is slower and smoother. |
| Normal Map | `Projected` uses the splat-derived normal map. `Keep Baked` preserves an existing normal map already wired into the mesh, for example from **Bake → Normal** in Optimize. |
| Normals | `Auto` uses covariance normals for flat, locally agreeing splats; the density gradient for moderate anisotropy; and clean mesh normals for volumetric or ambiguous clouds. `Splat Axis` forces covariance normals; `Clean Mesh` writes a flat map. |
| Normal Strength | Blends the projected normal toward the clean mesh normal. Lower it when the normal map fights the silhouette. |
| Fill Holes | Fills low-evidence texels from the nearest valid texel. Filled texels get zero confidence so repair tools can target them. |
| Fill Background | Dilates colour into the whole texture background, including UV poles and island gaps. Recommended with packed UV atlases. |
| Margin | Pixels of UV seam padding when Fill Background is disabled. |
| Pack Images | Stores the PNGs inside the `.blend`. Disable for external texture workflows. |

The external Python is the one configured in **Generate**. Projection does not
install packages or load PyTorch into Blender.

## How it works

For each texel covered by the UV atlas, the worker:

1. Rasterizes the mesh triangle to a world position, mesh normal, and UV
   tangent basis.
2. Finds the `K` nearest Gaussians.
3. Evaluates the anisotropic Gaussian field:

   ```text
   w_i = opacity_i * exp(-0.5 * (p - mu_i)^T Sigma_i^-1 (p - mu_i))
   color(p) = sum(w_i * color_i) / sum(w_i)
   coverage(p) = 1 - exp(-sum(w_i))
   ```

4. Derives a normal from the density gradient or covariance axes, orients it to
   the clean mesh, and blends it by **Normal Strength**.
5. Encodes the world normal into the mesh's tangent basis.
6. Fills low-confidence texels and pads UV seams before writing the PNGs.

The algorithm is deterministic; the only randomness is the splat data itself.
It uses NumPy/SciPy and a CPU worker. Typical local timings for 65k Gaussians
and a 2K texture are in the tens of seconds, depending on triangle count.

## What this is not

- It is not a true PBR material decomposition. Standard 3DGS has no albedo,
  roughness, metallic, or AO channels. Roughness/metallic must be authored or
  estimated separately; AO can be baked from the cleaned mesh.
- It is not a replacement for good geometry. A clean silhouette still comes
  from the mesh.
- It does not hallucinate missing surfaces. The confidence map exposes holes;
  a later texture-repair/inpainting mode can fill them with artist control.
- It does not modify or replace the splat source.

## Next steps

The base projection is deliberately small and deterministic. The Modddif-style
repair layer comes next:

- a layer stack with projected, painted, and generated patches;
- mask-based patch/inpaint from the current view, using the original input
  image and splat renders as references;
- normal-map brush/patch tools and a relighting preview;
- compositing shape, splat-detail, and AI-detail normals instead of choosing
  one normal source.

Those operations need cloud or additional local models; the deterministic
projection remains the source-faithful base layer.

## Relation to the Gaussian mesh research

The mesh can be approximate because appearance no longer has to survive as
vertex colors. This reduces the pressure on perfect splat-to-mesh extraction:
keep the research for silhouette, thin structures, and topology, but use
projection for the asset's look.

The independent `gaussian-surface-research` project studies Gaussian normals as
hypotheses with confidence. Its confidence work is relevant, but this add-on
feature remains self-contained and does not import that project.
