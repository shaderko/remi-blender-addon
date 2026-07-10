# Remi

A Blender addon that automates a complete mesh optimization pipeline:

**SDF Voxel Remesh → MeshLab Decimation → AutoRemesher (optional) → Texture Baking**

Import a GLB or use any mesh, and Remi produces an optimized, textured result — all on a copy, leaving your original untouched.

## Pipeline

| Step | Description | Always runs |
|------|-------------|-------------|
| **1. SDF Voxel Remesh** | Converts the mesh to an SDF grid via Geometry Nodes (`MeshToSDFGrid` → `GridToMesh`) and back. Parameters: detail slider (low→high resolution), optional fillet/smoothing. | ✅ Yes |
| **2. MeshLab Decimation** | Exports to PLY and runs PyMeshLab's `meshing_decimation_quadric_edge_collapse` for N passes at a configurable face reduction percentage. | ✅ Yes |
| **3. AutoRemesher** (optional) | Exports to OBJ and runs the external [AutoRemesher](https://github.com/huxingyi/autoremesher) CLI for quad-based retopology. Runs last in the pipeline when enabled. | 🔘 Toggle |
| **4. Bake Textures** | Bakes diffuse (albedo, no lighting), roughness, and normal maps from the original mesh onto the result. Maps can be baked together or independently. | 🔘 Default ON |

## Requirements

- **Blender 5.1+**
- **PyMeshLab** — installed automatically on first use via Blender's Python (`pip install pymeshlab`). Requires an internet connection.
- **AutoRemesher** (optional) — download from [github.com/huxingyi/autoremesher/releases](https://github.com/huxingyi/autoremesher/releases). Set the executable path in the addon panel or via the `AUTOREMESHER_PATH` environment variable.

## Download

[**Download the latest release**](https://github.com/shaderko/remi-blender-addon/releases/latest) (`remi_blender_addon_v*.zip`)

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

## UI Sections

| Section | Contents |
|---------|----------|
| **Active Mesh** | Current object name and vertex/face count |
| **SDF Voxel Remesh** | Detail slider, Fillet/Smooth toggles, SDF Remesh and Apply buttons |
| **AutoRemesher (External)** | Toggle (pipeline inclusion), executable path, target quads, adaptivity, edge scaling, sharp edge, smooth normal |
| **MeshLab Decimation** | Pass count, target percentage per pass, output name suffix |
| **Bake Textures** | Toggle (pipeline inclusion), texture size, optional automatic UV unwrap, and separate Albedo, Roughness, Normal, or all-map bake actions |
| **Remi Selection Tools** | Edit Mode bridge detection, smart lobe selection, preview split-part selection, and bridge splitting |
| **Full Pipeline** | One-click **▶ Run Full Remi** — runs all enabled steps |

## Notes

- The **original mesh is never modified** — everything runs on a copy.
- The final result's vertices are at world-space coordinates with the object at the origin.
- Baking uses Cycles with 128 samples, flat diffuse (no direct/indirect lighting), roughness, and tangent-space normal.
- For a manual bake, select one or more source/original meshes, then Shift/Command-select the remeshed target last so it is the active object. Disable **Auto Unwrap** to preserve externally prepared target UVs; baking will then require an existing UV map.
- The Edit Mode selection tools operate on the selected connected faces. Use **Smart Select Object** from a picked face, edge, or vertex to isolate a lobe before detecting or splitting its bridge.
- Intermediate format between Blender and PyMeshLab is **PLY binary** (dramatically faster than OBJ for large meshes).

## Credits

- **Remi** addon by [shaderko](https://github.com/shaderko)
- **AutoRemesher integration** adapted from [autoremesher-blender-bridge](https://github.com/adriflex/autoremesher-blender-bridge) by [Adriflex](https://adriflex.github.io/)
- **AutoRemesher** CLI tool by [huxingyi](https://github.com/huxingyi/autoremesher)
- **PyMeshLab** by Alessandro Muntoni and Paolo Cignoni / CNR-ISTI
- **MeshLab** by the Visual Computing Lab of ISTI-CNR

## License

GPL-3.0-or-later
