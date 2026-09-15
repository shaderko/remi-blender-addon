# Contributing to Remi

Remi is organized around one rule: a workflow feature may build a candidate
mesh, but only the session may replace the locked scene object or modify its
recovery history. Preserving that boundary keeps large meshes recoverable and
prevents the duplicate-object workflow that Remi Mode replaced.

Read [the architecture guide](docs/session_architecture.md) before changing a
workflow action.

## Find the right owner

| Change | Primary location |
| --- | --- |
| Feature name, actions, UI, validation, progress copy | `features/<feature>/feature.py` |
| Feature settings | `features/<feature>/settings.py` |
| Candidate-building use case | `features/<feature>/service.py` |
| Standalone Blender operator adapter | `features/<feature>/operators.py` |
| Repair algorithm | `features/repair/{boundary,manual,alpha_wrap,volume,guided}.py` |
| SDF Geometry Nodes mechanism | `features/remesh/geometry_nodes.py` |
| Bake images, materials, or Cycles execution | `features/bake/engine.py` |
| Back, Redo, Reset, checkpoint promotion | `workflow/history.py` |
| Automatic plan execution and preset persistence | `workflow/automatic.py`, `workflow/presets.py` |
| Default flow recipe, preset editor, automatic run operator | `app/flow.py` |
| Session action and interactive transactions | `workflow/session.py` |
| Temporary files and crash cleanup | `workflow/disk.py` |
| Blender object copying/loading/cleanup | `blender/session_objects.py` |
| Mesh file import/export | `blender/mesh_exchange.py` |
| Packaged and external tool integrations | `integrations/<tool>/` |
| Application assembly and registration | `app/` |
| Generic panel shell | `app/ui/` |
| Interactive Instant Meshes implementation | `features/retopology/instant_meshes/` |
| UV algorithms and native xatlas bridge | `features/uv/engine/` |
| Bridge and inner-shell edit tools | `features/edit_tools/` |
| Legacy operator compatibility only | `compat/` |

The repository root is the Blender extension boundary. Keep Python
implementation inside an owner package; root `__init__.py` should only delegate
registration to `app/`.

## Feature contract

A feature owns its descriptor, controls, messages, and action orchestration.
Its processing dependency is passed through the constructor using one of the
protocols in `features/contracts.py`. Concrete services are assembled explicitly
in `features.create_default_registry()`; Remi deliberately does not scan the
filesystem or mutate the session class at runtime.

For an atomic action:

1. The session writes `pending` and creates an independent working mesh.
2. The feature calls its injected service with that mesh.
3. The service returns a `StageResult` candidate and report; it does not commit.
4. The session validates and swaps the candidate, then promotes `pending` to
   `previous`.
5. Any exception removes transient data and keeps the locked mesh unchanged.

Interactive actions use the same boundary and must finish through
`commit_interactive_step` or `abandon_interactive_step`.

To expose an action to full-flow presets, mark it `automatic=True` and provide
feature-owned `preflight_automatic` and `draw_automatic_settings` methods. It
must be atomic and runnable without gesture payloads. Automatic execution calls
the same feature service through the same session transaction as manual use.

## Adding a workflow feature

1. Create `features/<name>/feature.py`, `service.py`, `settings.py`, and any
   focused mechanisms the feature needs.
2. Add a narrow service protocol to `features/contracts.py` and inject its
   implementation into the feature constructor.
3. Register one explicitly constructed feature in `features/__init__.py`.
4. Return candidates; never replace the session object, write checkpoints, or
   implement Back/Redo inside the feature.
5. Add architecture and Blender regressions for the action, failure cleanup,
   object count, and recovery behavior.

## Stable external contracts

Preserve published Blender operator IDs, scene properties, session recovery,
and saved-file behavior when moving implementation. Internal Python module
paths are not a public API and should not be preserved with root forwarding
modules. Put intentionally retained legacy product behavior under `compat/`
and test the user-visible contract through its canonical owner.

## Validation

Run the architecture regression while iterating:

```bash
/Applications/Blender.app/Contents/MacOS/Blender \
  --background --factory-startup \
  --python tests/blender_architecture_regression.py
```

Run the complete release gate before handing off a change:

```bash
./scripts/release_check.sh "/Applications/Blender.app/Contents/MacOS/Blender"
```

The release gate exercises registration, transactions, manual repair,
Back/Redo, UV, baking, edit tools, Instant Meshes, archive construction, and a
real extension installation. A passing build proves those automated paths; it
does not replace interactive viewport testing for UX changes.

The installed-extension regression executes real voxel remeshing, bundled
MeshLab decimation, UV generation, and all-map baking with user site-packages
disabled. Missing or damaged packaged dependencies are tested separately as an
expected preflight failure before any scene changes.
