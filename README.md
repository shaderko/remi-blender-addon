<p align="center">
  <img src="assets/remi_logo.png" alt="Remi Logo" width="180"/>
</p>

# Remi

**Repair, simplify, retopologize, and rebake difficult meshes without leaving Blender.**

Remi is a Blender 5.1/5.2 add-on for turning dense, damaged, or fragmented source
geometry into a cleaner working mesh. It can close holes and cracks, rebuild a
surface, reduce triangle count, create guided quad topology with Instant Meshes,
and bake the source appearance onto the result.

Remi Mode keeps one mesh selected and locked for the workflow. A successful
stage replaces that working mesh in place; a failed stage leaves it untouched.
The source, previous step, and optional redo state are compressed checkpoints on
disk instead of permanent scene duplicates.

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
| Generate validated production UVs | Remi UV charting, repair, and xatlas packing |
| Transfer the original appearance | Albedo, roughness, normal, and AO baking |
| Work with fused parts or doubled shells | Edit Mode selection and separation tools |

## One mesh, one workflow

Select a mesh once, click **Start Remi**, and move through the focused stages:

```text
Repair -> Remesh -> Retopology -> UV -> Bake
```

Every completed stage advances the panel. **Back** restores the mesh from before
the latest committed stage, **Redo** restores the reverted result, and **Start**
returns directly to the original source. **Finish** keeps the current mesh;
**Cancel Session** restores the source.

Only the current mesh remains in the scene while the session is idle. Remi may
hold a temporary candidate while an operation is running, but it commits that
candidate only after the operation succeeds.

### Guided retopology

Use the Interactive Instant Meshes workspace when you want to see and influence
the quad flow:

```text
Source mesh -> Solve fields -> Draw surface guides -> Preview quads -> Accept
```

Draw an **Orientation Comb** to steer nearby quad directions, or an **Output
Edge** guide when the extracted topology should follow a particular path with
an edge. Accepting the preview commits it to the same locked mesh.

## Download and install

[**Download the latest release**](https://github.com/shaderko/remi-blender-addon/releases/latest)

1. In Blender, open **Edit -> Preferences -> Get Extensions**.
2. Open the top-right menu, choose **Install from Disk**, and select the downloaded zip.
3. If Remi is disabled, enable it under **Preferences -> Add-ons**.
4. In the 3D Viewport, press `N` and open the **Remi** tab.

For development, build the same extension archive that users install:

```bash
./scripts/build_extension.sh "/Applications/Blender.app/Contents/MacOS/Blender"
```

Then use Blender's **Install from Disk** action with the archive written to
`dist/`. Do not also copy the repository into Blender's legacy
`scripts/addons` directory; two installations can register the same operator
IDs and invalidate testing.

Maintainers can run the complete headless regression suite and build a
validator-checked extension archive with:

```bash
./scripts/release_check.sh "/Applications/Blender.app/Contents/MacOS/Blender"
```

The release archive is written to `dist/`.

## Quick start

### Run a Remi session

1. Select a mesh in **Object Mode**.
2. Open **N-panel -> Remi** and click **Start Remi**.
3. Run only the stages the mesh needs. A successful stage becomes the new
   working mesh and opens the next stage automatically.
4. Use **Back**, **Redo**, or **Start** without finding or reselecting another
   object.
5. Click **Finish** to keep the current mesh, or **Cancel Session** to restore
   the original source.

### Create guided quad topology with Instant Meshes

1. Open **Retopo** inside an active Remi session.
2. Choose the approximate **Target** face count and click **Start Interactive Retopology**.
3. Wait for the native solve and initial quad preview to finish.
4. Use **Orientation Comb** or **Output Edge**, then drag with the left mouse button on the visible mesh surface. Release to re-solve the fields; with auto-update enabled, Remi also rebuilds the quad preview.
5. Use **Dim Original**, **Retopo Offset**, and **Face Fill** to make the cage easier to read. Enable **X-Ray Retopo** only when you deliberately want to see the back side.
6. Click **Accept Retopology** to replace the locked mesh, or **Cancel Retopology** to keep the mesh from before the interactive step.

The target count is approximate. Instant Meshes generates a field-aligned layout;
the guides influence that layout rather than acting as manually drawn topology.

### Generate a UV map

1. Open **UV** inside an active Remi session.
2. Choose a profile and the texture resolution used to calculate padding.
3. Keep **Padding** at `4 px` for dense general-purpose packing, or raise it for
   more conservative mip and bake isolation.
4. Enable **Preserve Marked Seams** when artist seams must remain locked.
5. Click **Generate UV**. The result summary reports chart count,
   95th-percentile stretch, and true occupied area in the UV tile.

Remi UV runs directly on an accepted Instant Meshes result. Small local
foldovers produced by a Blender unwrap solver are isolated and repaired without
discarding the rest of the valid chart layout.

### Repair holes

1. Open **Repair** inside an active Remi session.
2. For one visible hole, click **Draw Around Hole**, draw on the intact surface
   around its rim, and release. The local patch becomes a normal Remi step, so
   **Back** and **Redo** work without creating another scene object.
3. For automatic repair, choose **Boundary** for clear topology holes, **Hybrid** for holes plus narrow
   cracks, or a guided method for fragmented scan/AI geometry.
4. Click **Run Repair**. Remi keeps the current mesh unchanged if preparation
   fails and advances to **Remesh** after a successful commit.

## Choosing a repair method

| Method | Best for | Trade-off |
|--------|----------|-----------|
| **Voxel Remesh** | Fast general cleanup and surface consolidation | Rebuilds the whole surface and can soften fine detail |
| **Closing Volume** | Fragmented meshes, cracks, and holes that must be closed automatically | Slow and memory intensive; fits the result back to source surfaces and sharp creases |
| **Targeted Hole Patching** | One visible, ambiguous hole | Requires drawing around each hole, but commits only the local patch and supports Back/Redo |
| **Alpha-Guided Patches** | Heavily fragmented or AI-generated geometry | Requires the optional CGAL helper; uses the wrap only to find donor patches |
| **Boundary Only** | Clear, bounded topology holes | Does not bridge spatial cracks or disconnected fragments |
| **Hybrid** | A mix of boundary holes and narrow cracks | More aggressive than boundary filling alone |
| **Volume-Guided Patches** | Filling gaps while retaining most source triangles before remeshing | Uses a finer temporary volume and therefore costs more memory |

## Requirements and optional dependencies

### Core

- **Blender 5.1 or Blender 5.2 LTS**.
- The current release bundles native CPython 3.13/arm64 modules for Interactive
  Instant Meshes and xatlas-backed UV charting/packing on **macOS on Apple
  Silicon**. No standalone Instant Meshes app, xatlas installation, Homebrew,
  CMake, or compiler is needed for these bundled modules.

### Optional features

- **PyMeshLab** is required only for MeshLab Decimation. Remi never downloads
  it silently. In Blender's Python Console, run `import sys; print(sys.executable)`,
  close Blender, then install it from Terminal with:

  ```bash
  "/path/printed/by/blender/python3.13" -m pip install --user pymeshlab
  ```

  Reopen Blender afterward. If you do not need decimation, disable that stage;
  repair, remeshing, Instant Meshes, UVs, and baking do not require PyMeshLab.
- **Alpha-Guided Patches** requires CGAL and CMake. On macOS, run
  `brew install cgal cmake`; on Ubuntu/Debian, run
  `sudo apt install libcgal-dev cmake`. Remi can build its small helper
  automatically, or you can click **Build Helper**.
- **AutoRemesher** requires a separate executable from the
  [AutoRemesher releases page](https://github.com/huxingyi/autoremesher/releases).
  Set its path in the Remi panel or with the `AUTOREMESHER_PATH` environment
  variable.

## Session behavior

| Stage | What happens |
|-------|--------------|
| **Repair** | Commits either a manually drawn local patch or an automatic repair only when successful |
| **Remesh** | Applies Voxel Remesh or Closing Volume to a temporary candidate |
| **Reduce Faces** | Optionally commits a MeshLab-decimated candidate before retopology |
| **Retopology** | Opens guided Instant Meshes or runs optional external AutoRemesher |
| **UV** | Generates and validates the working mesh's atlas transactionally |
| **Bake** | Loads the source checkpoint automatically and bakes it onto the working mesh |
| **Back / Redo / Start** | Swaps disk checkpoints into the same visible object identity |

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
| **Run Remesh** | Builds an isolated candidate, applies the result, and replaces the locked mesh only after success. |
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
| **Accept Retopology** | Commits the current preview to the locked mesh and preserves a Back checkpoint. |
| **Cancel Retopology** | Releases the native workspace and keeps the pre-retopology mesh. |

</details>

<details>
<summary><strong>Decimation, AutoRemesher, and baking controls</strong></summary>

| Control | Meaning |
|---------|---------|
| **Decimation Passes** | Number of sequential PyMeshLab decimation passes. |
| **Keep** | Fraction of faces retained per pass. For example, six `50%` passes retain roughly `1.56%` before topology limits. |
| **Preserve Detail** | Enables normal preservation and planar quadrics during decimation. |
| **Keep Texture (standalone only)** | Uses MeshLab's texture-aware decimation to preserve the object's UVs and image texture. |
| **AutoRemesher Target / Adaptive** | Requested quad count and curvature-adaptive density. |
| **Edge Scale / Sharp / Smooth** | External AutoRemesher edge scaling, sharp-angle threshold, and normal smoothing angle. |
| **Texture Size** | Square output resolution for every baked map. |
| **Generate UV Map** | Runs the standalone Remi UV analyzer, chart generator, unwrap, repair, pack, and validation pipeline on the active mesh. |
| **UV Profile** | Selects coordinated decisions for balanced assets, texture painting, normal baking, lightmaps, hard surfaces, organic meshes, or scans/AI meshes. |
| **UV Padding** | Sets exact xatlas island padding in texture pixels (4 px by default). |
| **Preserve Marked Seams** | Keeps artist-authored seam edges as hard chart boundaries during regeneration. |
| **Auto Unwrap** | Generates target UVs when none exist or the existing map fails collapse/flip/overlap validation. Valid UV maps are retained. |
| **UV Method** | Uses Remi UV by default; Blender Smart Project and Lightmap Pack remain explicit compatibility fallbacks. |
| **Recalc Normals** | Recalculates target normals before baking. |
| **Half Scale** | Temporarily scales both meshes to `0.5x` during baking, then restores them. |
| **Cage / Max Ray** | Cage extrusion and maximum source-ray distance. |

Inside Remi Mode, the original source is loaded from its recovery checkpoint
automatically. Use **Bake All Maps** or bake albedo, roughness, normal, and AO
independently; Back restores the pre-bake mesh and Redo restores the baked data.

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

## Remi UV

Remi UV is a first-class UV stage rather than a single projection call. The
current automatic pipeline:

1. analyzes connected components, manifold boundaries, materials, sharp edges,
   local angles, face topology, and principal object axes;
2. classifies the input as planar, hard-surface, organic, cylindrical, or
   irregular;
3. preserves marked seams and creates angle-, material-, cylinder-, or
   PCA-directional chart boundaries as appropriate;
4. parameterizes with Blender 5.1 Minimum Stretch, with Angle Based and
   Conformal solver retries;
5. measures per-triangle conformal distortion and adds local chart boundaries
   around high-stretch regions;
6. equalizes island scale and sends the existing charts through multiple
   xatlas packing searches with exact texture-pixel padding, principal-axis
   pre-rotation, free-orientation bases, and exhaustive placement on small
   atlases up to 512 px;
7. benchmarks Remi charts against both Blender Smart Project and xatlas' native
   3D chart generator, scoring occupied area, fragmentation, distortion, and
   validity instead of accepting the first successful unwrap;
8. rejects non-finite, collapsed, locally flipped, or overlapping UV triangles,
   retains the least-damaged Blender solver attempt, and repairs small mirrored
   regions as face-local charts; if every Blender parameterizer remains invalid,
   xatlas chart generation provides an independent recovery path;
9. repairs any remaining vertex-fan foldovers by re-projecting only a minimal
   set of conflicting faces as independent micro-charts, then repacks and
   validates the complete atlas again.

Packing occupancy is measured from triangle area in the final UV tile. xatlas'
padded texel utilization is also logged separately, making padding cost visible
instead of hiding it in a fractional Blender margin. Artist-authored seams are
treated as locked constraints; full re-charting candidates are skipped when
those constraints are present.

The standalone **Generate UV Map** button regenerates the active UV map. The
baking pipeline first validates an existing map and keeps it when valid; a blank
or invalid map is regenerated when Auto Unwrap is enabled. Generated seams
remain visible and editable in Blender for artist cleanup.

On the 7,381-triangle launcher reference mesh at 2048 px with 4 px padding, the
new pipeline reduced fragmentation from 1,018 to 413 charts while increasing
true tile occupancy from 10.7% to 61.5%. The selected result measured 1.17 p95
conformal stretch and zero overlapping or flipped triangles. These numbers are
a regression reference for that asset, not a guaranteed density for every mesh.

The headless regression suite covers planar, hard-surface, cylindrical,
organic, toroidal, irregular triangulated, disconnected, and non-manifold
fixtures, as well as deterministic output and Edit Mode state restoration:

```bash
/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup \
  --python tests/blender_uv_regression.py
```

The Instant Meshes integration smoke test also accepts a generated quad mesh
and immediately validates it through Remi UV:

```bash
/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup \
  --python tests/blender_instant_meshes_smoke.py
```

## Baking notes

- Baking uses Cycles with 128 samples.
- Albedo is baked from Principled **Base Color** through emission so metallic
  source materials do not wash it out.
- Normal maps are tangent-space; AO is stored as non-color data.
- Source materials are deep-copied before temporary bake changes.
- Images are reused by name, so rebaking updates existing maps.

## Project structure

- [`CONTRIBUTING.md`](CONTRIBUTING.md) maps common changes to their owning
  modules. [`docs/session_architecture.md`](docs/session_architecture.md)
  describes the transaction and dependency rules in detail.
- [`features/`](features/) contains the five injected workflow features. Each
  feature owns its descriptor, UI, settings, Blender adapters, and use-case
  service. Repair strategies, Remesh Geometry Nodes, the Retopology-native
  Instant Meshes workspace, the complete UV engine, and the Bake engine all
  live beside their owning feature. Auxiliary edit tools live in
  `features/edit_tools/`.
- [`app/`](app/) is the extension composition boundary: dependency assembly,
  registration lifecycle, and the shared workflow panel shell.
- [`workflow/`](workflow/) contains the stable single-mesh session coordinator,
  bounded history transitions, observable state, disk lifecycle, feature
  contracts, and the feature registry. It does not know how Repair, UV, or
  Bake algorithms work.
- [`blender/`](blender/) owns Blender object/data-block mechanics and mesh file
  exchange used by the workflow.
- [`integrations/`](integrations/) contains Alpha Wrap, AutoRemesher, and
  PyMeshLab clients, including the Alpha Wrap helper source and external
  decimation worker.
- [`compat/`](compat/) contains only intentionally retained legacy product
  behavior. It is not a general home for forwarding imports.
- The standalone Instant Meshes GUI, NanoGUI, GLFW, OpenGL renderer, and CLI are
  intentionally not included because Blender supplies those responsibilities.
- The repository root is deliberately limited to Blender's `__init__.py` and
  manifest plus project metadata. New Python implementation belongs to one of
  the owners above.

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
