<p align="center">
  <img src="remi_logo.png" alt="Remi Logo" width="180"/>
</p>

# Remi

**Repair, simplify, retopologize, and rebake difficult meshes without leaving Blender.**

Remi is a Blender 5.1+ add-on for turning dense, damaged, or fragmented source
geometry into a cleaner working mesh. It can close holes and cracks, rebuild a
surface, reduce triangle count, create guided quad topology with Instant Meshes,
and bake the source appearance onto the result.

Repair, remesh, decimation, and retopology create separate results. The source
mesh stays untouched; standalone baking writes only to the target you select.

The Interactive Instant Meshes workspace uses the **actual native Instant Meshes
field solver and quad extraction code** inside Blender. It is not a slow Python
rewrite and it does not launch the old standalone application. The native C++
core does the heavy processing; Blender provides the UI, viewport preview, and
surface-drawing tools.

<p align="center">
  <a href="https://youtu.be/eR4afAdbMeU">
    <img src="https://img.youtube.com/vi/eR4afAdbMeU/0.jpg" alt="Remi Demo" width="560" height="315">
  </a>
  <br>
  <a href="https://youtu.be/eR4afAdbMeU">Watch the Remi demo →</a>
</p>

## What Remi does

| Goal | Remi tool |
|------|-----------|
| Repair holes, cracks, and fragmented geometry | Voxel Remesh, Closing Volume, or targeted hole patches |
| Make a dense mesh lighter | Multi-pass PyMeshLab decimation |
| Create and guide a quad layout | Interactive Instant Meshes inside the Blender viewport |
| Run automatic external quad remeshing | Optional AutoRemesher integration |
| Transfer the original appearance | Albedo, roughness, normal, and AO baking |
| Work with fused parts or doubled shells | Edit Mode selection and separation tools |

## Two ways to work

### Automated optimization

Use **Run Full Remi** to chain the enabled non-interactive stages:

```text
Source mesh -> Repair / Remesh -> Decimate -> [AutoRemesher] -> [Bake textures]
```

Each stage can also run on its own. AutoRemesher and texture baking are optional.

### Guided quad retopology

Use the Interactive Instant Meshes workspace when you want to see and influence
the quad flow:

```text
Source mesh -> Solve fields -> Draw surface guides -> Preview quads -> Accept
```

This is a hands-on workspace, not a stage in **Run Full Remi**. Draw an
**Orientation Comb** to steer nearby quad directions, or an **Output Edge** guide
when the extracted topology should follow a particular path with an edge.

## Download and install

[**Download the latest release**](https://github.com/shaderko/remi-blender-addon/releases/latest)

1. In Blender, open **Edit -> Preferences -> Add-ons**.
2. Click **Install from Disk** and select the downloaded zip.
3. Enable **Remi** in the add-on list.
4. In the 3D Viewport, press `N` and open the **Remi** tab.

For development or a manual installation from this repository:

```bash
python3 install_blender_addon.py --blender-version 5.1
```

Restart Blender after a manual installation.

## Quick start

### Create guided quad topology with Instant Meshes

1. Select a mesh in **Object Mode**.
2. Open **N-panel -> Remi -> Instant Meshes (Interactive)**.
3. Choose the approximate **Target** face count and click **Start Interactive Retopology**.
4. Wait for the native solve and initial quad preview to finish.
5. Use **Orientation Comb** or **Output Edge**, then drag with the left mouse button on the visible mesh surface. Release to re-solve the fields; with auto-update enabled, Remi also rebuilds the quad preview.
6. Use **Dim Original**, **Retopo Offset**, and **Face Fill** to make the cage easier to read. Enable **X-Ray Retopo** only when you deliberately want to see the back side.
7. Click **Accept Retopology** to create a new Blender mesh.

The target count is approximate. Instant Meshes generates a field-aligned layout;
the guides influence that layout rather than acting as manually drawn topology.

### Run the automated pipeline

1. Select a mesh in **Object Mode**.
2. Open the **Remi** tab in the 3D Viewport sidebar.
3. Enable the stages you need and expand them to adjust their settings.
4. Start with **Voxel Remesh** for speed. Use **Closing Volume** when filling gaps is more important than runtime and memory use.
5. Click **Run Full Remi** and follow the progress shown in Blender.

### Patch one visible hole

1. Select **Voxel Remesh** and frame the hole in the viewport.
2. Click **Draw Around Hole** under **Targeted Hole Patching**.
3. Draw on the intact visible surface around the rim, not through the empty hole.
4. Release to create a separate `_targeted_patch` preparation mesh.
5. Run **Remesh Copy** on that prepared copy.

## Choosing a repair method

| Method | Best for | Trade-off |
|--------|----------|-----------|
| **Voxel Remesh** | Fast general cleanup and surface consolidation | Rebuilds the whole surface and can soften fine detail |
| **Closing Volume** | Fragmented meshes, cracks, and holes that must be closed automatically | Slow and memory intensive; fits the result back to source surfaces and sharp creases |
| **Targeted Hole Patching** | One visible, ambiguous hole | Requires drawing around each hole, but changes only the local preparation mesh |
| **Alpha-Guided Patches** | Heavily fragmented or AI-generated geometry | Requires the optional CGAL helper; uses the wrap only to find donor patches |
| **Boundary Only** | Clear, bounded topology holes | Does not bridge spatial cracks or disconnected fragments |
| **Hybrid** | A mix of boundary holes and narrow cracks | More aggressive than boundary filling alone |
| **Volume-Guided Patches** | Filling gaps while retaining most source triangles before remeshing | Uses a finer temporary volume and therefore costs more memory |

## Requirements and optional dependencies

### Core

- **Blender 5.1+**.
- The current release bundles the native CPython 3.13/arm64 Interactive Instant
  Meshes module for **macOS on Apple Silicon**. No standalone Instant Meshes app,
  Homebrew, CMake, or compiler is needed for this bundled module.
- **PyMeshLab** is installed automatically into Blender's Python environment on
  first use. The first installation requires an internet connection.

### Optional features

- **Alpha-Guided Patches** requires CGAL and CMake. On macOS, run
  `brew install cgal cmake`; on Ubuntu/Debian, run
  `sudo apt install libcgal-dev cmake`. Remi can build its small helper
  automatically, or you can click **Build Helper**.
- **AutoRemesher** requires a separate executable from the
  [AutoRemesher releases page](https://github.com/huxingyi/autoremesher/releases).
  Set its path in the Remi panel or with the `AUTOREMESHER_PATH` environment
  variable.

## Pipeline behavior

| Stage | What happens | Full pipeline default |
|-------|--------------|-----------------------|
| **Repair / Remesh** | Creates a rebuilt copy with Voxel Remesh or Closing Volume | On |
| **MeshLab Decimation** | Reduces faces through one or more quadric-collapse passes | On |
| **Interactive Instant Meshes** | Opens the guided viewport workspace and extracts a quad result | Separate manual workflow |
| **AutoRemesher** | Sends a mesh to the optional external automatic quad remesher | Off |
| **Bake Textures** | Bakes albedo, roughness, tangent-space normal, and AO maps to the result | On |

## UI reference

The short descriptions below are a reference for less common settings. Blender
also shows a tooltip when you hover over a control.

<details>
<summary><strong>Repair and remesh controls</strong></summary>

| Control | Meaning |
|---------|---------|
| **Voxel Size** | SDF sampling resolution. Lower values preserve more detail but use more memory. |
| **Volume Resolution** | Closing-volume voxel size relative to Voxel Size. `0.5` is twice as fine and substantially more expensive. |
| **Crack Size** | Largest volumetric gap to close relative to the object bounds. Start low and increase only until the intended gaps close. |
| **Surface Fit Reach** | Distance around retained volume patches that is projected back onto the source. |
| **Preserve Sharp Creases** | Fits nearby reconstructed vertices toward detected source feature edges. |
| **Feature / Reach** | Minimum crease angle and the width of crease fitting measured in final voxels. |
| **Fillet / Smooth** | Optional post-remesh SDF refinement. |
| **Remesh Copy** | Creates a remeshed object while keeping the source. |
| **Apply Modifier** | Applies the Voxel Remesh Geometry Nodes modifier permanently to the copy. |
| **Ray px** | Pixel spacing between samples for a targeted hole stroke. Lower values follow the stroke more densely. |
| **Depth** | Rejects ray hits whose visible-surface depth changes too much, helping avoid the back surface through a hole. |
| **Patch Resolution / Relax** | Controls targeted or guide-derived patch tessellation and interior smoothing. Patch borders remain locked. |
| **Pre-Repair Holes** | Runs a selected hole-preparation method before normal Voxel Remesh. |
| **Start / Maximum Hole Scale** | Initial and maximum opening scale used by Alpha-Guided Patches. |
| **Auto Find Hole Scale** | Increases the hidden guide scale until enough open boundaries are covered. |
| **Boundary Coverage** | Required fraction of sampled open edges that must meet generated patches. |
| **Surface Offset** | How tightly the hidden Alpha Wrap guide follows the source near hole borders. |
| **Hole Detection** | Minimum guide-to-source distance treated as missing surface. Lower values fill smaller gaps. |
| **Border Overlap** | Extra guide-face rings retained around each patch so the following voxel stage can fuse it. |
| **Max Loop Edges** | Largest explicit boundary loop that Boundary Only or Hybrid may cap. |
| **Weld Distance** | Merges nearly coincident vertices before boundary analysis. Zero disables welding. |
| **Recover Detail / Detail Reach** | Projects reconstructed vertices toward nearby source surfaces without moving the centers of newly filled gaps. |
| **Helper / Auto Build / Build Helper** | Select, automatically compile, or explicitly compile the CGAL Alpha Wrap helper. |

</details>

<details>
<summary><strong>Interactive Instant Meshes controls</strong></summary>

| Control | Meaning |
|---------|---------|
| **Target** | Approximate output face count. Pure-quad subdivision is accounted for automatically. |
| **Pure Quads** | Regularly subdivides the extracted field mesh into quads only. |
| **Creases / Angle** | Aligns the field to source edges sharper than the selected angle. |
| **Align Open Boundaries** | Constrains the field and output grid to open mesh boundaries. |
| **Extrinsic** | Optimizes directions in 3D instead of relying only on intrinsic surface transport. |
| **Deterministic** | Prefers reproducible hierarchy operations at a small performance cost. |
| **Projection Steps** | Number of output smoothing and source-surface reprojection passes. |
| **Start Interactive Retopology** | Creates a persistent native session and starts the orientation and position solves. Auto-update then builds the first preview. |
| **Orientation Comb** | Draws a surface guide that steers nearby quad directions. |
| **Output Edge** | Guides direction and asks extraction to place an output edge along the stroke. |
| **Dim Original** | Darkens the source while preserving normal depth occlusion. |
| **Retopo Offset** | Lifts the cage along its normals to prevent z-fighting with the source. |
| **Face Fill** | Adds translucent faces beneath the bright preview edges. |
| **X-Ray Retopo** | Shows the entire cage through the source, including its back side. |
| **Orientation / Position** | Shows the native fields in the viewport. |
| **Singularities** | Shows orientation and position field singularities. |
| **Auto-update After Guides** | Automatically re-extracts the preview after guide-driven field solves. |
| **Rebuild Both Fields** | Rebuilds orientation and position while retaining guides. Auto-update also rebuilds the preview. |
| **Re-solve Position** | Rebuilds the position field without discarding the orientation result. |
| **Update Quad Preview** | Re-extracts the quad result from the current fields. |
| **Accept Retopology** | Creates a new Blender object from the current preview. |
| **Cancel Session** | Releases the native session and removes its overlays. |

</details>

<details>
<summary><strong>Decimation, AutoRemesher, and baking controls</strong></summary>

| Control | Meaning |
|---------|---------|
| **Decimation Passes** | Number of sequential PyMeshLab decimation passes. |
| **Keep** | Fraction of faces retained per pass. For example, six `50%` passes retain roughly `1.56%` before topology limits. |
| **Preserve Detail** | Enables normal preservation and planar quadrics during decimation. |
| **AutoRemesher Target / Adaptive** | Requested quad count and curvature-adaptive density. |
| **Edge Scale / Sharp / Smooth** | External AutoRemesher edge scaling, sharp-angle threshold, and normal smoothing angle. |
| **Texture Size** | Square output resolution for every baked map. |
| **Auto Unwrap** | Generates target UVs when none exist. Disable it to use UVs prepared elsewhere. |
| **UV Method / Margin** | Automatic unwrap method and spacing between UV islands. |
| **Recalc Normals** | Recalculates target normals before baking. |
| **Half Scale** | Temporarily scales both meshes to `0.5x` during baking, then restores them. |
| **Cage / Max Ray** | Cage extrusion and maximum source-ray distance. |

For standalone baking, select the source mesh or meshes first and the target last,
so the target is active. Use **Bake All Maps** or bake albedo, roughness, normal,
and AO independently.

</details>

<details>
<summary><strong>Edit Mode tools</strong></summary>

| Tool | Meaning |
|------|---------|
| **Smart Select Object** | Selects the complete connected island from a picked face, edge, or vertex. |
| **Detect Volume Bridges** | Finds narrow connectors between meaningful spatial volumes. |
| **Preview Fused Part** | Selects one side of the proposed volume-aware separation. |
| **Separate Fused Volumes** | Separates fused parts across all detected connector edges. |
| **Select Inner Shell** | Previews the likely inner duplicate layer from nearby opposite-facing surfaces. |
| **Remove Inner Shell** | Deletes the detected inner layer and optional direct connector faces. Preview first. |

</details>

## Baking notes

- Baking uses Cycles with 128 samples.
- Albedo is baked from Principled **Base Color** through emission so metallic
  source materials do not wash it out.
- Normal maps are tangent-space; AO is stored as non-color data.
- Source materials are deep-copied before temporary bake changes.
- Images are reused by name, so rebaking updates existing maps.

## Project structure

- Blender UI and orchestration are written in Python.
- [`instant_meshes/`](instant_meshes/) contains the isolated Blender-facing
  Interactive Instant Meshes implementation.
- [`instant_meshes/native/`](instant_meshes/native/) contains the headless C++
  field solver, pybind11 bridge, build files, and retained upstream source.
- The standalone Instant Meshes GUI, NanoGUI, GLFW, OpenGL renderer, and CLI are
  intentionally not included because Blender supplies those responsibilities.
- PyMeshLab, CGAL, and AutoRemesher integrations remain separate from the
  Instant Meshes module.

## Credits

- **Remi** by [shaderko](https://github.com/shaderko).
- **Instant Meshes** by Wenzel Jakob and contributors —
  [wjakob/instant-meshes](https://github.com/wjakob/instant-meshes).
- **AutoRemesher integration** adapted from
  [autoremesher-blender-bridge](https://github.com/adriflex/autoremesher-blender-bridge)
  by [Adriflex](https://adriflex.github.io/).
- **AutoRemesher** by [huxingyi](https://github.com/huxingyi/autoremesher).
- **PyMeshLab** and **MeshLab** by CNR-ISTI's Visual Computing Lab.
- **CGAL 3D Alpha Wrapping**, based on Portaneri et al., *Alpha Wrapping with an
  Offset* (SIGGRAPH 2022).

## License

[GPL-3.0-or-later](LICENSE). The native Interactive Instant Meshes module
contains compatible third-party components. See
[Third-party notices](THIRD_PARTY_NOTICES.md) for exact revisions, licenses,
and retained-source details.
