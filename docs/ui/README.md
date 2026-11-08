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

Measured against the real 5.3 gallery (13 images), with one image selected:

| | Idea | Rows | Widgets |
|---|---|---|---|
| **1** | Prompt bar, 3-column thumbnails, one action bar below | 12.5 | 27 |
| **2** | Three numbered steps: Describe → Pick → Make 3D | 19.8 | 31 |
| **3** | One large preview, thumbnails as a strip, actions under | 10.8 | 26 |
| **4** | Compact sidebar + wide floating browser popover | 7.7 (+5.3 sheet) | 12 |
| today | Prompt, 3×2 cards with two buttons each, detail box, mesh controls | 21.2 | 48 |

(20 px per line, approximate; today with nothing selected is 15.8 rows.)

What actually differs, beyond height:

- **One button per card.** Today every card carries a play button *and* a select
  button; the variants keep selection on the card and move 3D to the detail
  block. Cards become thumbnails again instead of toolbars.
- **Selection is visible.** Today the selected card uses `box.alert` — a red box.
  Variants use a radio indicator plus the depress state.
- **The 3D step has a home.** Variant 2 numbers it; variant 1 puts it under the
  grid with the mesh controls; variant 4 puts browsing in its own wide surface,
  where thumbnails can actually be big.
- **Settings stay one click away** in the add-on's existing gear popover
  (`Remi_PT_ImageSettings`), reused rather than reimplemented.

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
| Generate variant | *(open)* |
| Keep one gear for settings, or split image/3D | *(open)* |
| Thumbnails in sidebar or in a sheet | *(open)* |
| Where the 3D step lives | *(open)* |
| Port target | `features/images/panel.py: draw_create` + `_draw_gallery` |
| Test assertions to replace | `tests/blender_workspace_ui_regression.py` (prompt-only, one popover) |
