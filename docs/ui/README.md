# UI design pass

Two sketch playgrounds, both throwaway-safe. No add-on code is involved.

- `../../scripts/ui_generate_sketch.py` — **current focus**: four layouts for the
  Generate workspace (image prompt, thumbnails, 3D source).
- `../../scripts/ui_sketch.py` — the earlier session-shell variants, **shelved**
  (see the decision log).
- [`census-before.md`](census-before.md) — what the sidebar draws today, per state.
- `../../scripts/ui_census.py` — regenerates that report.

## Run

Double-click **`scripts/run-ui-sketch.bat`** — it finds Blender 5.3, launches
with the Generate variants, and keeps the console open on failure. Pass a name
for the other playground: `run-ui-sketch.bat ui_sketch.py`.

Without the launcher, use absolute paths (a relative `--python` from the wrong
folder silently opens a normal Blender with no sketch):

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe" --factory-startup `
  --python "C:\Users\skaredko\Documents\projects\personal\remi-cloud\remi-blender-addon\scripts\ui_generate_sketch.py"
```

Or inside Blender: Scripting workspace → Text Editor → Open → Run Script.

Then press `N`: the sidebar tabs run down the right edge of the viewport
(`Item`, `Tool`, `View`, `Animation`, `Remi`, …). The sketches add a **`GEN`**
tab next to the add-on's own **`Remi`** tab, so both are one click apart.

The tabs only exist while a sketch script runs. The `GEN` panels read the real
scene and the real gallery folder, and their buttons are the production
operators — a variant can actually drive image generation and 3D creation.

## Generate variants

Driving constraint from the first review: **thumbnails were too small, and they
should be the control point for 3D generation.** So every card now carries its
own 3D action and its own progress; there is no separate detail panel to visit.

| | Idea | Rows | Preview |
|---|---|---|---|
| **A** | One full-width card per row; 3D starts from the card | 22.9 | `scale=7` ≈ 220 px |
| **B** | Two cards per row, same per-card action | 13.5 | `scale=3.2` ≈ 100 px |
| **C** | One large preview, thumbnails below to switch | 13.1 | hero 7, strip 1.6 |
| today | 3×2 cards, two buttons each, detail box, mesh controls | 21.2 | `scale=2.4` ≈ 75 px |

Blender note: a scaled preview (`template_icon`) cannot itself be clicked — only
operators and props are interactive. So the action is a full-width button
directly under each thumbnail, and all secondary actions (load settings, copy
prompt, open, delete) shrink to an icon row. Making the image itself a hit
target would need a custom GPU-drawn popup, which is a different kind of project.

Thumbnail size is one number per variant (`scale=` in the file) — tell me if 220 px
is still too small or already too tall.

## How to judge

For each variant, keep the same window width and check:

1. How many thumbnails can you see at once, and are they big enough to recognise?
2. Which action is primary after selecting an image — Generate 3D, or something
   else competing with it?
3. Does the prompt stay reachable while browsing (variant 4 hides it only in the
   sheet)?
4. Do you ever have to hunt for settings, or for the image you just made?
5. Height: does the whole thing fit one screen at 320 px wide?

Also open the **Remi** tab next to `GEN` and flip between them — the comparison
that matters is against today's layout, on your own gallery.

## Findings so far

- **The gallery does filesystem work inside `draw`** (`gallery.refresh`,
  `Path.is_dir`). Cached by token, but still redraw-time I/O; the sketch was
  written to call the same functions, so this is worth fixing regardless of the
  winning layout.
- **Image generation and 3D generation share one surface.** The 3D entry points
  only exist inside a selected image's detail block, which is why the flow feels
  scattered rather than sequential.
- **One gear hides everything.** Model, size, count, seed, negative prompt,
  Python path, model folder, mesh quality and output all live in
  `Remi_PT_ImageSettings`; a first-time user sees none of it.
- **`feature.draw()` conflates settings and actions** (from the session pass).
  Any layout that relocates stage controls duplicates the run buttons until
  that seam gets a `draw_settings()` / `draw_actions()` split.
- **Interactive retopology** (42.6 rows) has no good sidebar treatment; not this
  pass, but noted.

## Budget proposal

| State | Rows | Rule |
|---|---|---|
| Generate, idle | ≤ 14 | prompt + one primary + thumbnails visible |
| Generate, one selected | ≤ 18 | detail replaces, never appends |
| Generate, busy | ≤ 10 | progress + cancel, thumbnails still visible |
| Browser sheet | own surface | wide enough for 4+ columns |

## Decision log

| Question | Decision |
|---|---|
| Session shells (A–D) | **Rejected** — all felt worse than today; Optimize stays as-is |
| Thumbnail size | Must be much larger; currently the main complaint |
| Thumbnails as 3D control point | Required — per-card action, not a detail panel |
| Generate variant | *(open: A, B, or C)* |
| Preview scale | *(open: A/B use one number each)* |
| Keep one gear for settings, or split image/3D | *(open)* |
| Port target | `features/images/panel.py: draw_create` + `_draw_gallery` |
| Test assertions to replace | `tests/blender_workspace_ui_regression.py` (prompt-only, one popover) |
