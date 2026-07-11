# Remi

A Blender addon that automates a complete mesh optimization pipeline:

**Voxel Remesh or Fitted Closing Volume → MeshLab Decimation → AutoRemesher (optional) → Texture Baking**

Import a GLB or use any mesh, and Remi produces an optimized, textured result — all on a copy, leaving your original untouched.

## Pipeline

| Step | Description | Always runs |
|------|-------------|-------------|
| **1. Repair / Remesh** | Choose the fast original Voxel Remesh or the slower hole-closing volume reconstruction with surface and sharp-crease fitting. | ✅ Yes |
| **2. MeshLab Decimation** | Exports to PLY and runs PyMeshLab's `meshing_decimation_quadric_edge_collapse` for N passes at a configurable face reduction percentage. Progress is reported live in the UI. | ✅ Yes |
| **3. AutoRemesher** (optional) | Exports to OBJ and runs the external [AutoRemesher](https://github.com/huxingyi/autoremesher) CLI for quad-based retopology. Runs last in the pipeline when enabled. | 🔘 Toggle |
| **4. Bake Textures** | Bakes albedo, roughness, normal, and ambient-occlusion maps from the original mesh onto the result. Each channel can be baked together or independently. | 🔘 Default ON |

## Requirements

- **Blender 5.1+**
- **PyMeshLab** — installed automatically on first use via Blender's Python (`pip install pymeshlab`). Requires an internet connection.
- **CGAL + CMake** (for Alpha-Guided Hole Patches) — macOS: `brew install cgal cmake`; Ubuntu/Debian: `sudo apt install libcgal-dev cmake`. Remi builds its small native helper automatically, or you can click **Build Helper**.
- **AutoRemesher** (optional) — download from [github.com/huxingyi/autoremesher/releases](https://github.com/huxingyi/autoremesher/releases). Set the executable path in the addon panel or via the `AUTOREMESHER_PATH` environment variable.

## Download

[**Download the latest release**](https://github.com/shaderko/remi-blender-addon/releases/latest)

## Installation

### Option 1: Blender Install from Disk (recommended)

1. Download the zip from the [releases page](https://github.com/shaderko/remi-blender-addon/releases/latest)
2. In Blender: **Edit → Preferences → Add-ons → Install from Disk**
3. Select the downloaded zip
4. Enable **Remi** in the addon list

### Option 2: Manual install

```bash
# From the remi directory:
python3 install_blender_addon.py --blender-version 5.1
```

Then restart Blender and enable it in **Edit → Preferences → Add-ons → Remi**.

The addon panel appears in the 3D Viewport sidebar under the **Remi** tab (`N` key).

## UI

The panel is organized into collapsible sections. Each pipeline step has a toggle that shows/hides its options and enables/disables it in the full pipeline run.

### Remesh
| Control | Description |
|---------|-------------|
| **Method: Voxel Remesh** | Original fast Remi flow. Supports optional local pre-repair and leaves a Geometry Nodes modifier for preview before applying. |
| **Method: Closing Volume** | Expensive alternative that reconstructs a finer closing volume, projects it back to source faces, and fits vertices around detected sharp edges—including concave inside corners. It returns concrete geometry and can replace the voxel stage. |
| **Volume Resolution** | Closing-volume voxel size relative to Voxel Size. `0.5` is twice as fine and substantially more expensive. |
| **Preserve Sharp Creases** | Enables explicit feature-edge fitting after surface projection. |
| **Feature °** | Minimum source dihedral angle treated as a crease. `35°` is a useful starting point for 90° corners. |
| **Reach** | Width of crease fitting measured in final voxels. Increase if an inside corner remains rounded; decrease if nearby surfaces pinch. |
| **Targeted Hole Patching** | Fast local repair with no volume reconstruction. Rays are cast only along the drawn stroke to form an ordered 3D ring on the visible surrounding surface. Remi triangulates and fairs a membrane inside that ring. |
| **Ray px** | Pixel spacing between stroke rays. Lower values follow irregular drawings more densely. |
| **Depth** | Allowed front-surface depth variation relative to the object diagonal. Lower values reject rays that pass through the hole and hit a back surface. |
| **Patch Resolution / Relax** | Controls adaptive membrane tessellation and interior fairing. The ray-hit boundary remains locked on the source. |
| **Draw Around Hole** | Starts the viewport stroke. Draw on the intact surface around one hole—not through empty space—and release. The result preserves the original mesh and adds only that local overlapping membrane. |
| **Pre-Repair Holes** | Enables a local hole-preparation method before the normal SDF stage. |
| **Method: Alpha-Guided Hole Patches** | Recommended for fragmented AI geometry. CGAL creates a temporary watertight guide, but Remi discards almost all of it. Only faces spanning regions far from the original surface are copied onto an untouched duplicate of the source. The prepared source and patches then enter the normal voxel flow. |
| **Detail Scale** | Controls which openings the hidden guide bridges. Raise it when the guide follows through a hole instead of covering it; lower values retain smaller cavities. |
| **Auto Find Hole Scale** | Repeats the hidden guide at progressively larger scales until generated patches touch the requested fraction of open boundaries. |
| **Maximum Scale** | Safety limit for automatic escalation. Extremely fragmented shells may need `0.3` or more. |
| **Boundary Coverage** | Required fraction of sampled open edges that should meet a donor patch. Remi reports the measured result and stops instead of silently continuing when coverage remains very poor. |
| **Surface Offset** | How tightly the hidden guide follows the source near hole borders. It does not offset the original triangles. |
| **Hole Detection** | Minimum guide-to-source distance treated as a missing-surface patch. Lower values fill smaller gaps but can capture intentional recesses. |
| **Border Overlap** | Number of guide-face rings added around a patch. Near-source border vertices are projected onto the original surface so the following voxel stage fuses the patch reliably. |
| **Patch Resolution** | Target patch edge length measured in SDF voxels. Lower values adaptively subdivide donor patches more densely without changing the hole-closing scale. |
| **Patch Relax** | Smooths only donor-patch interiors after subdivision. Patch borders stay locked to the original surface. |
| **Helper / Auto Build / Build Helper** | Use a custom helper binary, compile it on first run, or build it explicitly. The source is included in `alpha_wrap_helper/`. |
| **Method: Hybrid** | Caps bounded topology holes, then bridges fragmented cracks with an SDF expand/contract closing pass. |
| **Method: Boundary Only** | Triangulates explicit boundary loops without changing nearby disconnected surfaces. |
| **Method: Volume-Guided Patches** | Creates a finer temporary SDF closing volume, projects its near-source region onto the original, retains only faces spanning empty space, and discards the rough full shell before the normal remesh. |
| **Guide Resolution** | Temporary volume voxel size relative to the final voxel size. `0.5` makes the guide twice as fine; lower values hug the source more closely but use more memory. |
| **Surface Fit Reach** | Distance around retained volume patches that is projected exactly onto the original surface. This affects the donor patch only; original triangles are never replaced. |
| **Max Loop Edges** | Largest boundary loop that the topology stage may cap. |
| **Weld Distance** | Merges nearly coincident vertices before boundary analysis. Zero disables welding. |
| **Crack Size** | Maximum volumetric closing distance relative to the mesh bounds. Start low and increase only until the intended gaps close. |
| **Recover Detail** | Softly projects reconstructed vertices back toward nearby original surfaces without moving the centers of newly filled gaps. |
| **Detail Reach** | Maximum scale-aware projection distance. Remi also guarantees a minimum reach of two voxels. |
| **Repair Copy** | Runs the configured repair as a standalone operation while preserving the source. |
| **Voxel Size** | SDF sampling resolution (lower = finer). Remi also uses it as the Grid to Mesh threshold, preserving the original one-voxel consolidation behavior. |
| **Fillet** / **Smooth** | Optional post-remesh surface refinement. |
| **Remesh Copy** | Creates a remeshed copy of the active mesh. |
| **Apply Modifier** | Applies the Geometry Nodes modifier permanently. |

### MeshLab Decimation
| Control | Description |
|---------|-------------|
| **Passes** | Number of sequential decimation passes. |
| **Keep** | Target face count percentage per pass (e.g. 50% = half the faces each pass). |
| **Preserve Detail** | Enables MeshLab normal preservation and planar quadrics so decimation retains recovered surface structure. |
| **Suffix** | Name suffix for the output mesh. |
| **Decimate** | Run decimation standalone on the active mesh. |

### AutoRemesher (External)
| Control | Description |
|---------|-------------|
| **Executable** | Path to the AutoRemesher CLI binary. |
| **Target** | Target quad/face count. |
| **Adaptive** | Adaptivity parameter. |
| **Edge Scale** | Edge scaling factor. |
| **Sharp °** | Sharp angle threshold. |
| **Smooth °** | Smooth normal angle. |
| **Run AutoRemesher** | Run standalone on the active mesh. |

### Bake Textures
| Control | Description |
|---------|-------------|
| **Texture Size** | Output resolution for baked maps. |
| **Auto Unwrap** | When ON, generates UVs automatically (Smart Project or Lightmap Pack) if the target has no UV map. **Disable this** if you have already unwrapped the target mesh externally. |
| **UV Method** | Smart Project or Lightmap Pack (only shown when Auto Unwrap is ON). |
| **Margin** | UV island margin (only shown when Auto Unwrap is ON). |
| **Recalc Normals** | Recalculate normals on the target before baking. |
| **Half Scale** | Temporarily scale objects to 0.5× during baking for improved ray-hit reliability, then restore. |
| **Cage** | Cage extrusion distance. |
| **Max Ray** | Maximum ray distance for baking cast. |

**Baking buttons** (standalone — select source → target, then click):

| Button | Bakes |
|--------|-------|
| **Bake All Maps** | Albedo + Roughness + Normal + AO |
| **Albedo** | Diffuse/albedo only |
| **Roughness** | Roughness only |
| **Normal** | Tangent-space normal only |
| **AO** | Ambient occlusion only |

### Edit Mode Selection Tools
| Tool | Description |
|------|-------------|
| **Smart Select Object** | From a picked face, edge, or vertex, selects the entire connected mesh island. |
| **Detect Volume Bridges** | Finds every narrow connector between two meaningful spatial volumes, favoring similarly sized parts over similar face counts. |
| **Preview Fused Part** | Selects one side of the proposed volume-aware separation. |
| **Separate Fused Volumes** | Separates fused parts across all detected connector edges. |
| **Select Inner Shell** | Scans nearby opposite-facing surfaces and previews the likely inner duplicate layer. |
| **Remove Inner Shell** | Removes the detected inner layer and optional direct connector faces. Use the preview action first. |

### Full Pipeline
One-click **▶ Run Full Remi** — runs all enabled steps in sequence, with a progress bar and real-time status updates.

## Usage

### Automated pipeline
1. Select or import a mesh
2. Open the **Remi** tab in the sidebar (`N` key)
3. Choose **Voxel Remesh** for speed, or **Closing Volume** when automatic hole closure is worth the additional time and memory. For Closing Volume, start with Crack Size `0.015`, Volume Resolution `0.5`, Surface Fit Reach `0.03`, Feature `35°`, and Reach `2.5`
4. Toggle each remaining step on/off as needed and adjust parameters
5. Click **▶ Run Full Remi**

### Target one ambiguous hole
1. Choose **Voxel Remesh** and frame the visible hole in the viewport
2. Under **Targeted Hole Patching**, click **Draw Around Hole**
3. Hold the left mouse button and draw on the intact surface around the visible rim; release to build
4. Inspect the generated `_targeted_patch` copy
5. Keep Pre-Repair Holes disabled and run **Remesh Copy** on that prepared copy

### Standalone baking (manual)
1. **Select the original mesh(es)** with materials (source)
2. **Shift-select the optimized/remeshed mesh** so it becomes active (target)
3. Toggle **Auto Unwrap** OFF if you have prepared UVs on the target yourself
4. Click **Bake All Maps** (or Albedo / Roughness / Normal / AO individually)

### Edit Mode selection tools
1. Enter **Edit Mode** on a mesh
2. The **Remi Selection Tools** panel appears
3. Pick a face, edge, or vertex and click **Smart Select Object** to isolate a connected lobe
4. For accidentally fused parts, use **Detect Volume Bridges**, preview the result, then **Separate Fused Volumes**
5. For a fully doubled mesh, use **Select Inner Shell** first; if the preview is correct, run **Remove Inner Shell**

## How Baking Works

- Uses **Cycles** with 128 samples for high-quality results
- Albedo is baked from the source Principled **Base Color** through emission, so metallic source materials cannot wash it out.
- Normal maps are **tangent-space**
- AO is baked as a separate non-color data map.
- **Half-scale** mode (default ON) sets both objects to 0.5× scale during baking for reliable ray hits, then restores — no transform is applied
- Source materials are **deep-copied** so the original shaders are never touched
- Images are **reused by name** — re-baking updates existing maps rather than creating duplicates

## Notes

- The **original mesh is never modified** — everything runs on a copy.
- Alpha Wrap is used only as a cloth-like analysis surface. The delivered preparation mesh is the evaluated original geometry plus local donor patches; the full wrap is deleted before SDF remeshing. Use **Prepare Hole Patches** to inspect this intermediate mesh directly.
- Hybrid hole repair remains available and combines bounded boundary triangulation with an optional scale-aware SDF morphological closing pass.
- The final result's vertices are at world-space coordinates with the object at the origin.
- Intermediate format between Blender and PyMeshLab is **PLY binary** (dramatically faster than OBJ for large meshes).
- Each pipeline step can be run standalone via its own button — independent of the full pipeline.

## Credits

- **Remi** addon by [shaderko](https://github.com/shaderko)
- **AutoRemesher integration** adapted from [autoremesher-blender-bridge](https://github.com/adriflex/autoremesher-blender-bridge) by [Adriflex](https://adriflex.github.io/)
- **AutoRemesher** CLI tool by [huxingyi](https://github.com/huxingyi/autoremesher)
- **PyMeshLab** by Alessandro Muntoni and Paolo Cignoni / CNR-ISTI
- **MeshLab** by the Visual Computing Lab of ISTI-CNR
- **CGAL 3D Alpha Wrapping** by the CGAL project, based on Portaneri et al., *Alpha Wrapping with an Offset* (SIGGRAPH 2022)

## License

GPL-3.0-or-later
