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

Driving constraint from the second review: **cells are wanted, but the image
must fill the cell.** So a cell is now the preview plus one slim button row —
no box, no seed line, no prompt text, no tool row inside the cell. Everything
else lives once above the grid, and secondary actions (load settings, copy,
open, delete) moved into a per-image `▾` menu.

| | Idea | Rows | Image | Fits at |
|---|---|---|---|---|
| **A** | 1-up | 12.7 | `scale=7` ≈ 224 px | 320 px |
| **B** | 2-up | 10.7 | `scale=3.8` ≈ 122 px | 280 px |
| **C** | 3-up | 10.7 | `scale=2.8` ≈ 90 px | 300 px |
| **D** | 2-up wide | 9.7 | `scale=6.5` ≈ 208 px | ~460 px |
| **E** | adaptive (`grid_flow`) | 8.7 | `scale=8` ≈ 256 px | 1 column at 320 px, 2 at ~600 px |
| today | 3×2 boxed cards at `scale=2.4` | 21.2 | ≈ 75 px in a ~110 px box | — |

Each cell is the preview plus one row: the 3D action, `⤢` (open full size in an
Image Editor), and `▾` for the rest. Small variants drop `⤢` and keep two slots.

**E is A and D merged**: the tile size is fixed at 256 px, and `grid_flow(columns=0)`
decides how many fit, so the default narrow sidebar shows one big image per row
and a widened one shows two. Square (1×1) output means the tile wastes no space
on letterboxing.

The hard limit: a preview does not grow with the panel. `template_icon` draws at
`scale × 32 px`, so the column count has to match the sidebar width you actually
use. Wider sidebar does not enlarge the images — it only fits more columns (see
variant D, which needs ~460 px).

Preview check on this machine: in a GUI Blender the gallery previews load
(ids 1111+, `icon_size` 32×32), in `--background` they do not (id 0), so a
headless screenshot only ever shows the fallback icon. Judge the size in the
app, not from my captures.

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

- **Overlays are not available in a panel.** `UILayout` has no z-order and no
  absolute positioning; a scaled preview is display-only and cannot carry text
  or buttons on top of it. Panels also deliver no double-click — every panel
  widget is a single-click operator or prop. Three ways to get closer,
  cheapest first:
  1. **Tile + controls directly beneath** (what the sketch does): image, then a
     slim row in the same block, plus a `⤢` button that opens the image in a
     real Image Editor (verified: the operator splits the area and loads the
     file). The image is ~95 % of the cell; the controls are not on it.
  2. **State baked into the thumbnail**: a coloured bar or border composited
     into the PNG (numpy over image pixels). Gives state at a glance, no text,
     and needs a thumbnail cache with invalidation.
  3. **Custom GPU browser**: a modal operator with a `SpaceView3D` draw handler,
     GPU textures, `blf` text and its own hit-testing — real hover overlays,
     clickable images, double-click, keyboard navigation. This is the only way
     to get controls *on* the image, and it is a separate surface: how
     BlenderKit and asset shelves do it.
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
| Cells vs single image | Multiple cells wanted |
| Chrome inside a cell | **Removed** — image fills the cell, one slim button row |
| Thumbnails as 3D control point | Required — per-cell action, no detail panel |
| Generate variant | *(open: A, B, C, or D)* |
| Column count / target sidebar width | *(open — this is the size decision)* |
| Port target | `features/images/panel.py: draw_create` + `_draw_gallery` |
| Test assertions to replace | `tests/blender_workspace_ui_regression.py` (prompt-only, one popover) |
