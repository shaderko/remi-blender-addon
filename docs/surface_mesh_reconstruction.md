# Surface-aware optimization of Gaussian renders

This is a substantive alternative to density extraction or treating learned
depths as measurements. It **reoptimizes a separate Gaussian representation**
against the frozen source's calibrated RGB/alpha renders, with depth distortion,
normal consistency, and splat flattening regularization, before extracting a
camera-aware occupancy mesh. The original Gaussians are untouched. This pipeline
now powers **Create Mesh** (Dense by default, with a Fast direct-extraction mode).
See [Blender setup and requirements](image_generation.md#create-a-mesh). The
commands below remain available for research comparisons and held-out evaluation.

The [recorded ship pilot](surface_mesh_pilot_results.md) improves held-out
agreement after adding explicit silhouette constraints. Raw extraction alone is
not acceptable; inspect the negative ablations as well as the final candidates.

The external candidate is [Spirula Studio](https://github.com/harry7557558/spirula-studio),
release **v2026.10.16**, Windows Vulkan. It has native surface-oriented training
and meshing without installing CUDA extensions or repairing WSL. Its GPL-3.0
executable is **not bundled with Remi**. Blender installs it separately only after
explicit confirmation and verifies both the 49 MB official archive and executable
against pinned SHA-256 hashes. GPL-3.0 source/license links are retained in the
installation provenance. Research commands can use a separate ignored tool folder.

The local first launch passed camera checks but crashed inside an automatically
loaded Overwolf graphics hook. No Overwolf process was running. The two
process-local switches below allowed optimization to start; no registry or
system configuration was modified. The failed runs and logs are preserved.

## Run

Prepare native views with the multiview renderer first; see
[posed reconstruction](multiview_mesh_reconstruction.md). The optimizer receives
**only training views**, never held-out views. Gaussian centers are initialization,
not independent SfM ground truth. Neither DA3 nor native Gaussian depth is used.

```powershell
python -B scripts/prepare_surface_optimizer.py

python -B scripts/run_surface_reconstruction.py --views generations/my_views --initial-splats generations/my_source/model.ply --output generations/my_surface_run --iterations 8000 --cap 1000000
```

`--initial-splats` must be the original **3DGS PLY**, not a mesh or the NPZ
exchange archive. Every stage needs fresh output folders. The runner:

- Re-exports training images, original alpha masks, exact cameras, and Gaussian
  center initialization into its own COLMAP-text input directory.
- Checks the external parser's actual camera matrices and scale **before**
  training. Camera normalization/reorientation is disabled to retain the archive
  world frame. Parser disagreement is a blocker, not silently compensated by ICP.
  The pinned Windows debug dump does not escape backslashes in filename strings;
  the runner parses only the required numeric calibration fields and saves a
  separately validated JSON record, without changing the upstream executable.
- Starts no web viewer or network listener. Does not enable per-image exposure
  correction, learned depth/normal supervision, or camera optimization.
- Disables only Overwolf's two optional Vulkan hooks in the optimizer's child
  environment (`DISABLE_VULKAN_OW_OVERLAY_LAYER=1` and
  `DISABLE_VULKAN_OW_OBS_CAPTURE=1`), using the switches declared in the installed
  layer manifests. These layers can load with no Overwolf process running. No
  registry entries, global environment, or running applications are changed.
- Uses the upstream `meshing` preset, with explicit synthetic-view settings.
  The pilot uses full 1512 px training renders without resizing, one million splat
  capacity, geometry warmups at 1000 iterations, and densification until 6000.
  Eight thousand iterations is an experiment, not an established quality optimum.
- Disables extraction edge merging, component deletion, hole filling, unseen
  vertex culling, and quality relaxation. Retains dense triangles. Camera-aware
  occupancy uses `carve-k=3`, explicitly recorded in the command/report.
  The upstream extractor still splits non-manifold vertices automatically; this
  is retained in its log, not described as a completely untouched extraction.
  Triangle-angle diagnostics also matter: dense marching-tetrahedra results can
  contain many sliver triangles and are not automatically UV/production-ready.
- Records the exact training/extraction commands, source/tool hashes, logs,
  mesh hashes, and topology diagnostics. No claim of manifoldness or quality is
  made merely because extraction succeeds.

Compare the resulting mesh and the legacy density baseline on the same held-out
cameras with both appearance and untextured clay:

```powershell
python -B scripts/compare_reconstruction_meshes.py --views generations/my_views --mesh density=generations/my_density/density_shipped.ply --mesh surface=generations/my_surface_run/extraction/mesh.ply --blender 'C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe' --output generations/my_surface_comparison
```

Use the exact mesh path reported by the optimizer runner: upstream may suffix
its output filename. Large meshes are acceptable; missing ropes, noisy/doubled
surfaces, sealed cavities, or incorrect silhouettes are not. Texture transfer
and simplification happen only after surface quality is established.

### Explicit training-silhouette constraints

Reject geometry contradicted by the original training foreground, preserving
both the raw input and the rejection report. This is **not** arbitrary component
cleanup: the gate tests vertices and face centroids against the posed masks,
with explicit alpha/pixel tolerance and foreground/background support thresholds.
It cannot establish correct concave geometry or full mesh validity.

```powershell
python -B scripts/constrain_mesh_silhouettes.py --views generations/my_views --mesh generations/my_surface_run/extraction/mesh.ply --output generations/my_surface_constrained
```

Use the constrained mesh in `compare_reconstruction_meshes.py`. It defaults to
alpha 0.02, two pixels of foreground dilation, at least three foreground views,
and at most three background contradictions; unknown samples do not vote. These
are research settings to review across assets, not universal quality thresholds.

`run_surface_reconstruction.py --direct-extraction` runs the same camera-aware
mesher on the original splats without training. This ablation distinguishes
changes due to the extractor from changes due to surface-aware optimization;
it is explicitly labeled in the report and still checks calibration first.

`--visible-only` is a separate, explicit variant using the upstream
`--cull-unseen 1` visibility gate. It does not delete small components by size or
fill holes. Preserve the ungated mesh for comparison; inspect extraction logs
and the held-out images for detail lost by this gate. To re-extract an existing
trained model without retraining, pass its `splat.ply` as `--initial-splats`
together with `--direct-extraction` and a fresh output directory.

If this optimizer cannot handle a scene or fails the quality gate, preserve
that failure. Do not silently fall back to an unrelated mesh generator or
claim that this experiment solves reconstruction for single-image-generated
splats generally.

## Validation

```powershell
python -B -m unittest discover -s tests -p 'test_*reconstruction.py' -v
python -B -m unittest discover -s tests -p test_splat_mesh.py -v
python -B -m unittest discover -s tests -p test_camera_surface_worker.py -v
```

The pilot passed 40 reconstruction/safety tests, two unchanged density-worker
tests, and the Blender stereo-camera calibration regression. Synthetic tests
validate mechanics and input preservation, not general learned-model quality.

### Create Mesh integration checks (2026-10-25)

The packaged worker uses `integrations/splat_mesh/remi_surface/`; research CLIs
are not shipped or imported at runtime. Local Windows/Blender 5.3 beta checks:

- Real worker, **Fast**: 471,976 vertices / 943,412 triangles, 32.3 seconds.
- Real worker, **Dense**: 3,500,396 vertices / 6,996,604 triangles, 161.6 seconds.
- Real Blender modal **Dense** job: original archive deleted, transformed object,
  source renamed while running; source linkage/transform preserved, 7,127,590
  triangles imported without decimation. Optimization is not bitwise deterministic.
- Two independent objects in one Blender scene each produced their own Fast
  mesh (943,414 and 943,428 triangles), with only the matching source hidden.
- Real Blender modal cancellation during surface optimization passed: the owned
  worker stopped, the temporary archive was removed, and the source stayed
  visible with no mesh imported. An independent Windows test confirms that
  worker cancellation also terminates a native descendant process.
- 40 research reconstruction/safety tests, 13 packaged worker/exchange/installer/
  completion/cancellation tests, and two legacy density-baseline tests passed.
- Blender import/conversion, generation, workspace UI, and staged architecture
  regressions passed. A clean ZIP installation passed the extension smoke suite,
  including native modules, UVs, repair, full flow, and textured decimation.

These checks validate this machine and this asset, not other platforms, all
Blender 5.3 tools, or universal surface quality. Raw outputs and full stage logs
are retained under `generations/create_mesh_replacement_*` in the research checkout.
