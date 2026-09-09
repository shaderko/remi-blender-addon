# Remi session architecture

Remi edits one locked scene object through a session-owned transaction. Repair,
Remesh, Retopology, UV, and Bake never own history and never replace the scene
object themselves.

## Responsibilities

- `session.py` owns the active object, transaction lifecycle, Back/Redo/Reset,
  and temporary Blender data blocks. It refers only to named recovery slots and
  never creates, checks, moves, or removes files itself.
- `workflow/disk_service.py` allocates and retires every temporary directory.
  Its bounded recovery slots are `source`, `previous`, `pending`, and `redo`;
  every checkpoint is written atomically and validated before it can be used.
  Alpha Wrap, MeshLab, AutoRemesher, and the legacy full-pipeline operator receive
  service-owned scratch workspaces rather than creating global temp paths.
- `workflow/stages.py` owns the stage definitions and their order. A stage only
  transforms a session-provided working copy into a candidate result.
- `session_operators.py` adapts Blender UI commands to the session. It contains
  no mesh-processing logic.
- `operators.py`, `baking.py`, and the backend modules contain the actual mesh
  algorithms. They do not manage session history.

## Non-interactive transaction

Every stage runs through `RemiSessionRuntime.execute_stage`:

1. Save the current locked mesh to `pending.blend` and verify that the exact
   object and mesh data are present.
2. Create a private, independent mesh copy for the stage.
3. Run the stage against that copy. The locked scene object is read-only.
4. On success, replace the locked object with the candidate and promote
   `pending` to `previous`.
5. On failure, delete every candidate and working copy, discard `pending`, and
   leave both the locked object and existing history unchanged.

Only one previous result is retained because the product contract is “go back
to the mesh before the current step,” not an unbounded undo stack. The source
checkpoint is retained for Reset/Cancel and as the high-detail baking source.

## Interactive transaction

Interactive Retopology follows the same boundary. The session validates a
checkpoint and creates an unlinked working copy before the tool starts. The
tool may keep previews in its own runtime, but it must finish through
`commit_interactive_step` or `abandon_interactive_step`. The Outliner therefore
continues to show one locked object during the interaction.

Manual Repair is an interactive input gesture but a normal atomic stage: the
gesture collects the boundary, then `ManualRepair` receives a fresh working
copy and commits through `execute_stage`.

## Checkpoint identity

A `.blend` library fragment may include parent objects and other dependencies.
The adjacent JSON manifest records the exact object and mesh names, session ID,
and parent name. Recovery loads that recorded mesh explicitly; it never assumes
the first object in the fragment is the mesh. Older checkpoints without a
manifest fall back to the locked session object's exact name.

## Disk lifecycle and crash recovery

`SessionDiskService.create` creates the private directory and immediately writes
an owner marker containing the Blender process ID and session ID. Finish, Cancel,
failed session startup, and add-on unregister all close the service, which removes
the entire directory. Scratch workspaces are also released as soon as their
operation completes; closing the service remains the final cleanup boundary.

If Blender is terminated before that cleanup can run, add-on registration scans
only direct temporary-directory children named `remi-session-*`. It removes
directories owned by dead processes and leftovers from a previous add-on load in
the current process. It preserves directories owned by another live Blender
process. Ownerless or corrupt directories receive a five-minute grace period to
avoid racing a concurrently starting Blender, then are reclaimed on a later
start. No other temp directories are eligible for deletion.

## Adding a stage

1. Add a `WorkflowStage` subclass in `workflow/stages.py` with `command`,
   `label`, `next_stage`, and `build`.
2. Register one instance in `_STAGES`.
3. Accept the `working_copy` supplied to `build`; do not duplicate the locked
   object or write checkpoints inside the stage.
4. Return `(candidate, error, report)`. Returning an error or raising an
   exception causes a clean rollback.
5. Add a Blender regression covering success, Back, failure isolation, and a
   single visible scene object.

Stages that need the original source, currently Bake, set
`requires_source_checkpoint = True`. The session loads and cleans that temporary
source around the stage call.

## Long-running work

Queued stages publish their operation and scale before their timer executes, and
the session shows a wait cursor plus the measured completion time. Dense meshes
use the native xatlas route instead of repeatedly running every Python UV quality
candidate. A validated session UV is reused when its profile, padding, texture
size, topology counts, and active layer still match. Bake treats Auto Unwrap as
"unwrap when missing" and never repeats UV generation merely because baking was
started.
