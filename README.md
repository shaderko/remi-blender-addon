# Remi

A Blender addon that automates a complete mesh optimization pipeline:

**SDF Voxel Remesh → MeshLab Decimation → AutoRemesher (optional) → Texture Baking**

Import a GLB or use any mesh, and Remi produces an optimized, textured result — all on a copy, leaving your original untouched.

## Pipeline

| Step | Description | Always runs |
|------|-------------|-------------|
| **1. SDF Voxel Remesh** | Converts the mesh to an SDF grid via Geometry Nodes (`MeshToSDFGrid` → `GridToMesh`) and back. Adjustable detail, optional fillet/smoothing. | ✅ Yes |
| **2. MeshLab Decimation** | Exports to PLY and runs PyMeshLab's `meshing_decimation_quadric_edge_collapse` for N passes at a configurable face reduction percentage. Progress is reported live in the UI. | ✅ Yes |
| **3. AutoRemesher** (optional) | Exports to OBJ and runs the external [AutoRemesher](https://github.com/huxingyi/autoremesher) CLI for quad-based retopology. Runs last in the pipeline when enabled. | 🔘 Toggle |
| **4. Bake Textures** | Bakes diffuse (albedo, no lighting), roughness, and normal maps from the original mesh onto the result. Each channel can be baked together or independently. | 🔘 Default ON |

## Requirements

- **Blender 5.1+**
- **PyMeshLab** — installed automatically on first use via Blender's Python (`pip install pymeshlab`). Requires an internet connection.
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

### SDF Voxel Remesh
| Control | Description |
|---------|-------------|
| **Voxel Size** | Detail resolution (lower = finer). Applied to both MeshToSDFGrid voxel size and GridToMesh threshold. |
| **Fillet** / **Smooth** | Optional post-remesh surface refinement. |
| **Remesh Copy** | Creates a remeshed copy of the active mesh. |
| **Apply Modifier** | Applies the Geometry Nodes modifier permanently. |

### MeshLab Decimation
| Control | Description |
|---------|-------------|
| **Passes** | Number of sequential decimation passes. |
| **Keep** | Target face count percentage per pass (e.g. 50% = half the faces each pass). |
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
| **Bake All Maps** | Albedo + Roughness + Normal in one pass |
| **Albedo** | Diffuse/albedo only |
| **Roughness** | Roughness only |
| **Normal** | Tangent-space normal only |

### Edit Mode Selection Tools
| Tool | Description |
|------|-------------|
| **Smart Select Object** | From a picked face, edge, or vertex, selects the entire connected mesh island. |
| **Detect Bridge** | Finds and selects bridge edges between two connected mesh islands. |
| **Select Split Part** | Selects one side of a detected bridge. |
| **Split by Bridge** | Splits the mesh into separate parts at detected bridge edges. |

### Full Pipeline
One-click **▶ Run Full Remi** — runs all enabled steps in sequence, with a progress bar and real-time status updates.

## Usage

### Automated pipeline
1. Select or import a mesh
2. Open the **Remi** tab in the sidebar (`N` key)
3. Toggle each step on/off as needed, adjust parameters
4. Click **▶ Run Full Remi**

### Standalone baking (manual)
1. **Select the original mesh(es)** with materials (source)
2. **Shift-select the optimized/remeshed mesh** so it becomes active (target)
3. Toggle **Auto Unwrap** OFF if you have prepared UVs on the target yourself
4. Click **Bake All Maps** (or Albedo / Roughness / Normal individually)

### Edit Mode selection tools
1. Enter **Edit Mode** on a mesh
2. The **Remi Selection Tools** panel appears
3. Pick a face, edge, or vertex and click **Smart Select Object** to isolate a connected lobe
4. Use **Detect Bridge** to find bridge loops, then **Split by Bridge** or **Select Split Part**

## How Baking Works

- Uses **Cycles** with 128 samples for high-quality results
- Diffuse is baked as **flat albedo** (no direct/indirect lighting) — `use_pass_direct=false`, `use_pass_indirect=false`, `use_pass_color=true`
- Normal maps are **tangent-space**
- On metallic materials, metallic is temporarily forced to 0 on the baking source to prevent black diffuse results
- **Half-scale** mode (default ON) sets both objects to 0.5× scale during baking for reliable ray hits, then restores — no transform is applied
- Source materials are **deep-copied** so the original shaders are never touched
- Images are **reused by name** — re-baking updates existing maps rather than creating duplicates

## Notes

- The **original mesh is never modified** — everything runs on a copy.
- The final result's vertices are at world-space coordinates with the object at the origin.
- Intermediate format between Blender and PyMeshLab is **PLY binary** (dramatically faster than OBJ for large meshes).
- Each pipeline step can be run standalone via its own button — independent of the full pipeline.

## Credits

- **Remi** addon by [shaderko](https://github.com/shaderko)
- **AutoRemesher integration** adapted from [autoremesher-blender-bridge](https://github.com/adriflex/autoremesher-blender-bridge) by [Adriflex](https://adriflex.github.io/)
- **AutoRemesher** CLI tool by [huxingyi](https://github.com/huxingyi/autoremesher)
- **PyMeshLab** by Alessandro Muntoni and Paolo Cignoni / CNR-ISTI
- **MeshLab** by the Visual Computing Lab of ISTI-CNR

## License

GPL-3.0-or-later
