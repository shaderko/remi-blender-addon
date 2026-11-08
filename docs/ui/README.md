# UI shell design pass

Two artefacts, both throwaway-safe: this note, the generated census, and the
sketch playground in `scripts/`. No add-on code is involved.

- [`census-before.md`](census-before.md) — what the sidebar draws today, per state.
- `../../scripts/ui_census.py` — regenerates that report.
- `../../scripts/ui_sketch.py` — four candidate shells, live in Blender.

## Run the census

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
  --factory-startup --background --python scripts/ui_census.py
```

It registers the add-on, draws every state into a recording layout, restores
what it touched, and rewrites `docs/ui/census-before.md`. Run it again after a
shell change to compare numbers; keep the `before` copy if you want a diff.

Approximations: 20 px per line, 8 px per box, 6 px per separator. Good enough
to rank states, not to win an argument about pixels.

## Run the sketch

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" `
  --factory-startup --python scripts/ui_sketch.py
```

In the 3D viewport press `N`, open the **UI** tab, expand **Sketch Control**.

- **Demo** draws candidate shells against fabricated state — no session needed.
- **Live** draws the same shells against your real `remi_session`, so you can
  run an actual Repair → Remesh → UV → Bake pass through any variant.
- **Auto ✓** reloads the file about a second after you save it. Edit in your
  editor, watch Blender repaint.
- Always launch with `--factory-startup`: a second registered copy of the
  add-on collides with operator ids.

## The four shells

| | Idea | Session rows (Repair) | Trade-off |
|---|---|---|---|
| **A** | Tiered panel: header, one status line, stage menu, one box of controls, history | 25.4 | Smallest change from today; still one long scroll |
| **B** | Stage list: every stage a row, only the current one expanded | 27.3 | Best scannability, worst height |
| **C** | Compact rail: nine rows; the whole stage lives in a floating sheet | 8.3 | Fits one screen; hides parameters one click away |
| **D** | Checklist: six rows; stage settings open in a modal dialog | 9.3 | Cleanest rail; modal rhythm |

Today's session shell, for comparison: **27.6 rows / 552 px** (Repair) and
**42.6 rows / 852 px** (interactive retopology).

The entry view (no session) and the automatic full-flow view are shared by all
four, so the comparison is about the manual session loop only.

## How to judge

For each shell, capture the same matrix: sidebar at **320 / 420 / 560 px** ×
`UI scale` **1.0 / 1.5**, in Demo states *Stage (Repair)*, *Stage (Bake)*,
*Busy*, *Interactive*, and in Live after a real Remesh.

Look for:

1. Does the primary action sit on one screen with the status that justifies it?
2. Which labels truncate at 320 px, and does the truncation hide meaning?
3. Can you tell which stage you are in without reading a paragraph?
4. How many clicks/scrolled screens to: start → change stage → run → finish?
5. Does anything read as two competing primary buttons?

## Findings that came out of building this

- **`feature.draw()` conflates settings and actions.** Every shell that moves
  the stage to a sheet or dialog ends up with a duplicate run button, because
  the leaves carry their own. A `draw_settings()` / `draw_actions()` split is a
  prerequisite for C and D — small, mechanical, and behind the existing seam.
- **There is no per-stage completion state.** `remi_session` tracks history
  depth (`step_index`) and the current step name, not which stages ran. Any
  "done / next" affordance (B and D want one) needs a state addition, so it is
  a product decision, not just pixels.
- **Interactive retopology is the worst state in the panel** (42.6 rows) and no
  shell fixes it: it is a tool with its own viewport workspace, so it probably
  wants its own surface rather than another tier.
- **The gallery does filesystem work inside `draw`** (`gallery.refresh`,
  `Path.is_dir`), which is redraw-time I/O and a UX smell in equal measure.

## Proposed budget for the winning shell

| State | Rows | Rule |
|---|---|---|
| Entry | ≤ 8 | one primary |
| Session, manual | ≤ 16 | one primary, one status line, zero scrolling at 900 px |
| Busy | ≤ 10 | status + cancel only |
| Automatic | ≤ 6 | progress + stop |
| Interactive | own surface | not a sidebar tier |

## Decision log

Fill this in when a variant is chosen — then port only the shell.

| Question | Decision |
|---|---|
| Winning shell | |
| Tier each control moved to | |
| Leaf split needed (`draw_settings` / `draw_actions`) | |
| Per-stage completion state wanted | |
| Interactive state treatment | |
| Test assertions to replace | |
| Shell to port into `main_panel.draw_session` | |
