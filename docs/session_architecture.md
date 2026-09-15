# Remi architecture

Remi edits one locked scene object through a recoverable session. Features own
their product behavior; the session owns mesh history and transaction safety.
Adding or changing a feature should not require editing the main panel, session
runtime, or a central action switch.

## Composition

`app/application.py` is the composition root. Add-on registration creates one
`RemiApplication` containing:

- the singleton `RemiSessionRuntime` transaction engine;
- an ordered `FeatureRegistry` containing Repair, Remesh, Retopology, UV, and
  Bake.

`features.create_default_registry()` is deliberately explicit. Remi does not
scan the filesystem or monkey-patch the session at runtime, so feature order and
dependencies remain reviewable and deterministic.

Each feature implements the contract in `workflow/contracts.py` and owns:

- stable feature and action metadata;
- its panel controls and progress messages;
- action orchestration;
- its Blender operator classes;
- its scene setting declarations.

Feature-specific service interfaces live in `features/contracts.py`. The
composition root injects concrete use-case objects, so tests and downstream
developers can substitute one concern without monkey-patching module globals.
`features/settings.py` and `features/registration.py` then compose the Blender
properties and operator types contributed by those same instances.

## Automatic flows and presets

`app/flow.py` composes the built-in Remesh → Decimate → UV → Bake All recipe,
the preset editor, and the `remi.run_full_flow` operator. `workflow/presets.py`
stores versioned scalar JSON in the user configuration directory and validates
the entire settings document before assignment. Selection persists by preset
identifier rather than the dynamic enum's list position.

`workflow/automatic.py` freezes a plan, runs all feature-owned dependency
preflights, then calls the existing session's `execute_action` once per stage.
Each action still owns its own candidate and the session still owns all history
changes. Nothing routes through the legacy full-pipeline implementation.

Actions explicitly opt into automatic execution with `FeatureAction.automatic`.
Their features provide `preflight_automatic` and `draw_automatic_settings`;
interactive actions and actions requiring gesture payloads are not eligible.
The default recipe is four actions, but saved presets can choose other supported
actions while maintaining feature order. The new flow settings use a separate
`scene.remi_flow` facade, leaving `scene.remi_settings` and existing feature
settings compatible with saved files.

The operator shows a stage before executing it on the next timer tick. It ends
the session on complete success. On failure or stop, it becomes the existing
manual session controller so recovery buttons remain operational. The
`automatic` flag also blocks manually queued actions during a full flow.

The manual and automatic entry operators inherit shared behavior from the plain
Python `RemiSessionController`. Neither subclasses a registered Blender operator:
registering such a subclass can detach the original operator's RNA callbacks.
Entry-point regressions must call `bpy.ops.*.poll()` and execute both operators,
not only call the Python classes' `poll` methods.

Feature services contain mesh-processing behavior. They receive a working copy
from the session and return a `StageResult`; they never create recovery history
or replace the locked object themselves.

## Dependency direction

The active dependency flow is:

```text
registration / main UI / Blender command adapters
                    |
                    v
         application + feature registry
                    |
                    v
          workflow session transaction
                    |
                    v
              feature services
                    |
                    v
       Blender adapters / external integrations
```

Feature services may depend on lower-level mechanisms under their own feature,
reusable `blender` adapters, or clients under `integrations`. They must not
import the main panel or the session runtime. Manual Repair is the one adapter
that calls back into the session after its viewport gesture has collected input;
the geometry service itself still executes inside a normal session transaction.

## Directory responsibilities

- `features/<name>/feature.py`: metadata, UI, messages, and action orchestration.
- `features/<name>/settings.py`: settings contributed to the stable
  `scene.remi_settings` facade.
- `features/<name>/operators.py`: standalone Blender operator adapters.
- `features/<name>/service.py`: candidate construction for session actions.
- `features/remesh/geometry_nodes.py`: the reusable SDF node group and modifier
  mechanism used by Remesh and volume-guided Repair.
- `features/bake/engine.py`: Blender image, material, source-preparation, and
  bake execution mechanics behind the Bake service.
- `features/uv/engine`: UV profiles, mesh analysis, charting, packing, native
  xatlas integration, validation, and Blender orchestration.
- `features/retopology/instant_meshes`: interactive viewport workspace and its
  bundled native field solver.
- `features/edit_tools`: edit-mode bridge and double-shell tools outside the
  main processing sequence.
- `features/repair/boundary.py`, `alpha_wrap.py`, `volume.py`, `guided.py`, and
  `manual.py`: focused strategies and shared patch composition behind the
  repair use case.
- `workflow/session.py`: active object, working copies, commit, failure
  rollback, Back, Redo, Reset, Finish, and Cancel.
- `workflow/state.py`: Blender properties exposed to UI and operators.
- `workflow/history.py`: bounded checkpoint transitions and Back/Redo metadata.
- `blender/session_objects.py`: copying, checkpoint loading, object replacement,
  selection, and orphan data-block cleanup used by the session runtime.
- `workflow/session_operators.py`: Blender commands and timer adaptation only.
- `workflow/disk.py`: all session checkpoint and scratch-workspace disk
  lifecycle, including crash-leftover cleanup on add-on start.
- `app/ui/main_panel.py`: generic session shell and feature-registry rendering.
- `app/ui/source_view.py`, `session_header.py`, and `history_controls.py`: focused
  pieces of the session chrome, separate from feature-owned controls.
- `app/application.py` and `app/registration.py`: explicit dependency
  composition and Blender lifecycle order.
- `blender`: reusable object lifecycle and OBJ/PLY/GLB adapters.
- `integrations`: CGAL Alpha Wrap, AutoRemesher, and optional PyMeshLab clients;
  the MeshLab subprocess worker lives with its client rather than at add-on root.
- `compat`: operator IDs retained for older scripts and Blender files. The old
  multi-object full pipeline lives here and is not part of the primary UI.
- repository root: Blender entrypoint, extension manifest, and project metadata
  only; internal Python import paths are not preserved through forwarding files.

## Atomic actions

Every non-interactive action runs through
`RemiSessionRuntime.execute_action`:

1. Save and validate the current locked mesh in the `pending` recovery slot.
2. Create an independent working mesh for the selected feature action.
3. Load the original source checkpoint only when the action declares it needs
   one, currently texture baking.
4. Call the owning feature with a `FeatureExecutionContext`.
5. On success, replace the locked object with the returned candidate and
   promote `pending` to `previous`.
6. On failure, remove every candidate and working copy, discard `pending`, and
   leave the locked object and prior history unchanged.

Only one previous result is retained because the product contract is “return to
the mesh before the current step,” not an unbounded in-memory undo stack. The
source, previous, pending, and redo checkpoints live on disk so large scan and
generated meshes do not remain duplicated in RAM.

## Interactive actions

Interactive Retopology uses the same checkpoint boundary. The session writes
`pending` and creates an unlinked working copy before opening the tool. The
feature must finish through `commit_interactive_step` or
`abandon_interactive_step`; the Outliner therefore continues to show only the
locked session mesh.

Manual Repair is an interactive input gesture followed by the atomic
`MANUAL_REPAIR` action. The gesture collects projected ring points and normals,
then the session supplies a new working copy. Its action-level next feature is
Repair, so applying one patch does not unexpectedly advance the user to Remesh.

## Disk ownership and crash recovery

`workflow.disk.SessionDiskService` is the only owner of checkpoint files and temporary
operation directories. It writes checkpoints atomically with manifests that
identify the exact mesh, parent, materials, and session. Finish, Cancel, failed
startup, and unregister close the service and remove its directory.

If Blender terminates first, the next add-on registration scans only direct
temporary children named `remi-session-*`. It removes sessions owned by dead
processes and stale sessions from the current process, preserves other live
Blender instances, and gives ownerless or corrupt directories a five-minute
grace period.

## Adding a feature

1. Create `features/<name>/feature.py` with a `FeatureDescriptor` and stable
   `FeatureAction` IDs.
2. Implement `draw`, `execute` or `start_interactive`, `blender_classes`, and
   `scene_settings`. Reuse `FeatureDefaults` for unsupported paths.
3. Put heavy processing in a feature service. Accept the session's
   `working_copy`; do not duplicate the locked object or manage checkpoints.
4. Add the feature once to `create_default_registry()` in its desired order.
   Main navigation, action enums, settings, and operator registration are then
   composed from that registry.
5. Add a Blender regression covering success, failure isolation, a single
   visible object, Back/Redo, UI controls, and any source-checkpoint requirement.

Registry validation rejects duplicate feature IDs, duplicate action IDs, and
navigation targets that do not exist before Blender types are registered.
