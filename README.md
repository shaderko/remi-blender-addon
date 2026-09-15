<p align="center">
  <img src="assets/remi_logo.png" alt="Remi" width="150">
</p>

<h1 align="center">Remi</h1>

<p align="center">
  Repair, remesh, retopologize, unwrap, and rebake difficult meshes in Blender.
</p>

<p align="center">
  <a href="https://github.com/shaderko/remi-blender-addon/releases"><strong>Download latest beta</strong></a>
  · <a href="#quick-start">Quick start</a>
  · <a href="#tool-guide">Tool guide</a>
  · <a href="https://github.com/shaderko/remi-blender-addon/issues">Report an issue</a>
</p>

> [!WARNING]
> Remi 2.0 is beta software. Keep the original file and inspect every result.
> A stage may fail or produce unsuitable geometry on difficult meshes.

## What Remi does

| Need | Tool |
|---|---|
| Process a model end to end | Preset-driven **Run Full Flow** |
| Recover without scene duplicates | Single-mesh sessions with **Back**, **Redo**, and **Start** |
| Close holes, cracks, or fragmented surfaces | Manual patches, Boundary, Hybrid, Alpha-Guided, or Closing Volume repair |
| Rebuild a surface | Voxel Remesh or Closing Volume |
| Reduce triangles | Multi-pass MeshLab decimation |
| Create guided quads | Native Interactive Instant Meshes |
| Run automatic quad retopology | Optional AutoRemesher integration |
| Build production UVs | Validated charting, repair, and xatlas packing |
| Transfer source appearance | Albedo, roughness, normal, and AO baking |
| Separate fused parts or doubled shells | Edit Mode selection tools |

## Install

**Supported package:** Blender 5.1 or 5.2 on macOS Apple Silicon.

1. Download the newest ZIP from [GitHub Releases](https://github.com/shaderko/remi-blender-addon/releases).
2. In Blender, open **Edit → Preferences → Get Extensions**.
3. Open the top-right menu, choose **Install from Disk**, and select the ZIP.
4. In the 3D Viewport, press `N` and open **Remi**.

The package includes MeshLab, Alpha Wrap, native Instant Meshes, and the UV
backend. Do not unzip it or install a second copy in Blender's legacy
`scripts/addons` directory.

## Quick start

### Automatic: remesh to baked asset

1. Select one mesh in **Object Mode**.
2. Open **N-panel → Remi**.
3. Choose **Default · Remesh to Bake**.
4. Click **Run Full Flow**.

```text
Voxel Remesh → MeshLab Decimation → UV Unwrap → Bake All Maps
```

Remi checks dependencies before changing the mesh. Success leaves one finished
result. Failure stops before later stages and opens the last valid result as a
manual session; use **Back**, **Finish**, or **Cancel Session** from there.

### Manual: use only what the mesh needs

1. Select one mesh in **Object Mode** and click **Start Remi**.
2. Choose **Repair**, **Remesh**, **Retopology**, **UV**, or **Bake**.
3. Run an operation. Remi commits it only after success.
4. Click **Finish** to keep the result or **Cancel Session** to restore the source.

## Choose the right tool

| Problem | Start with | Key trade-off |
|---|---|---|
| General cleanup | **Voxel Remesh** | Fast; rebuilds the surface and may soften detail |
| Cracks and fragmented geometry | **Closing Volume** | More complete; slower and memory-heavy |
| One visible hole | **Draw Around Hole** | Precise local repair; requires a short surface stroke |
| Clear topology holes | **Boundary** | Preserves more geometry; does not bridge spatial gaps |
| Holes plus narrow cracks | **Hybrid** | More aggressive than Boundary |
| Difficult scan or AI fragments | **Alpha-Guided Patches** | Uses bundled MeshLab Alpha Wrap |
| Fewer triangles | **MeshLab Decimation** | Included in Remi |
| Artist-guided quads | **Interactive Retopology** | Target count is approximate |
| Automatic quads | **AutoRemesher** | Requires an external executable |
| UVs only | **Generate UV** | Existing marked seams can be preserved |
| Source textures on a new mesh | **Bake All Maps** | Uses Cycles and the original source checkpoint |

## Session safety

Remi locks one visible mesh for the active session. Operations run on temporary
candidates; a failed operation does not replace the current mesh.

| Control | Result |
|---|---|
| **Back** | Previous committed mesh |
| **Redo** | Reverted mesh |
| **Start** | Original source |
| **Finish** | Keep the current result and leave Remi |
| **Cancel Session** | Restore the source and leave Remi |

Recovery checkpoints are temporary session data, not crash-persistent project
history. Save the `.blend` file normally and keep source assets until the result
has been reviewed.

## Tool guide

<details>
<summary><strong>Full Flow and presets</strong></summary>

The built-in flow uses a `0.01` voxel size, six `50%` decimation passes (about
`1.56%` retained before topology limits), 2048 px textures, and a 4 px UV gap.
These are starting points, not universal settings.

- Click the pencil beside a preset to load it into **Current Settings**.
- Enable only the stages you need; use each stage's settings button to edit it.
- Save the current stages and settings as a reusable preset.
- Saving an existing name requires **Replace**. The built-in default cannot be deleted.
- **Stop After Current Stage** or `Esc` stops at the next stage boundary.

Interactive drawing and guided Instant Meshes are manual-only. Custom automatic
flows may include Repair or external AutoRemesher when their dependencies exist.

</details>

<details>
<summary><strong>Repair and remesh</strong></summary>

- **Draw Around Hole:** draw on intact visible surface around one hole; release to apply.
- **Boundary:** caps bounded topology loops.
- **Hybrid:** combines boundary filling with narrow-crack repair.
- **Alpha-Guided Patches:** uses bundled MeshLab Alpha Wrap as a guide for missing regions.
- **Volume-Guided Patches:** borrows only gap-spanning faces from a fine closing-volume guide.
- **Closing Volume:** closes spatial gaps, then fits the result toward source surfaces and creases.
- **Voxel Remesh:** fast, general surface consolidation.

Lower voxel sizes retain more detail and consume more memory. Start coarse, then
reduce the value only when the silhouette needs it.

</details>

<details>
<summary><strong>Interactive Instant Meshes</strong></summary>

1. Open **Retopology**, set an approximate target, and click **Start Interactive Retopology**.
2. Draw an **Orientation Comb** to steer flow or an **Output Edge** to guide an extracted edge.
3. Change **Target Faces** to re-solve without restarting the workspace.
4. Use **Dim Original**, **Retopo Offset**, and **Face Fill** to read the preview.
5. Click **Accept Retopology** or **Cancel Retopology**.

The preview can report topology warnings and remain acceptable. Treat warnings
as review prompts: inspect holes, components, and shading before accepting.

</details>

<details>
<summary><strong>UV</strong></summary>

Remi classifies the mesh, builds several chart candidates, repairs local invalid
regions, packs with xatlas, and keeps the best valid result within its distortion
and density limits.

- Choose a profile and the texture size used to measure spacing.
- **Gap** is edge-to-edge distance: `4 px` means four pixels across the gap.
- **Preserve Marked Seams** keeps artist seams as chart boundaries.
- The result reports chart count, 95th-percentile stretch, occupied area, overlaps, and gap.
- A failed UV operation restores the previous UVs and seams.

Validation establishes the measured checks; it does not guarantee optimal
packing or a fixed occupancy percentage for every mesh.

</details>

<details>
<summary><strong>Baking</strong></summary>

Remi bakes from the original source checkpoint onto the current result. Use
**Bake All Maps** or bake albedo, roughness, tangent-space normal, and AO separately.

- **Auto Unwrap** keeps a valid UV map and regenerates a missing or invalid one.
- **Auto Cage & Ray** derives distances from the source-to-target gap.
- **Half Scale** temporarily scales both bake meshes together, then restores them.
- **Recalc Normals** is off by default to preserve the UV-prepared target's normals.
- Baked images are Blender data; pack or save them before closing the file.

</details>

<details>
<summary><strong>Edit Mode cleanup</strong></summary>

The Remi tab adds two focused tool groups in Mesh Edit Mode:

- **Fused Parts:** smart-select a region, detect a narrow bridge, preview one side, and separate it.
- **Double Shell:** preview or remove a nearby, oppositely oriented inner layer and optional connectors.

Preview destructive selections before applying them.

</details>

## Optional dependency

Automatic quad retopology requires the optional
[AutoRemesher](https://github.com/huxingyi/autoremesher/releases) executable.
MeshLab decimation and Alpha-Guided repair are included in Remi.

## Beta limits

- A complex stage can keep Blender busy until it finishes; stopping occurs between stages.
- Geometry algorithms are input-sensitive. A successful run still needs visual inspection.
- Session recovery is not yet durable across a Blender crash or restart.
- Bundled native binaries currently target macOS Apple Silicon and Blender's CPython 3.13.
- Report reproducible failures with the mesh, failing stage, settings, and Blender version.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for code ownership and
[session_architecture.md](docs/session_architecture.md) for workflow contracts.

```bash
# Build the installable extension
./scripts/build_extension.sh "/Applications/Blender.app/Contents/MacOS/Blender"

# Run regressions, build, install, and smoke-test the archive
./scripts/release_check.sh "/Applications/Blender.app/Contents/MacOS/Blender"
```

## Credits and license

Remi is free and open source under [GPL-3.0-or-later](LICENSE). See
[Third-party notices](THIRD_PARTY_NOTICES.md) for Instant Meshes, xatlas, Eigen,
TBB, and other bundled components.
