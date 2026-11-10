"""Baseline census of the Remi sidebar.

Records every widget the add-on draws for each state a user can be in, then
approximates how many lines / pixels each state costs. It changes no add-on
code and restores the properties it touches before exiting.

    blender --factory-startup --background --python scripts/ui_census.py

The markdown report is written to docs/ui/census-before.md. Run it again after
a shell change to compare numbers.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "ui" / "census-before.md"

LEAF_KINDS = {
    "label",
    "prop",
    "operator",
    "popover",
    "icon",
    "progress",
    "template_list",
    "menu",
}
NOTE_ICONS = {
    "INFO",
    "ERROR",
    "CHECKMARK",
    "TIME",
    "LOCKED",
    "CANCEL",
    "BRUSH_DATA",
    "FILE_REFRESH",
}
LINE_PX = 20.0
BOX_PX = 8.0
SEPARATOR_PX = 6.0
EMPHASIS = 1.25


# --------------------------------------------------------------------------
# Recording layout: mimics the UILayout API the panels use.
# --------------------------------------------------------------------------


class Layout:
    def __init__(self, kind, parent=None):
        self.kind = kind
        self.parent = parent
        self.children = []
        self.scale_y = parent.scale_y if parent is not None else 1.0
        self.enabled = parent.enabled if parent is not None else True
        self.alert = False
        self.use_property_split = False
        self.use_property_decorate = False
        self.text = ""
        self.icon = ""
        self.name = ""
        self.panel = ""
        self.depress = False
        self.emboss = True
        self.slider = False

    # containers ----------------------------------------------------------
    def column(self, **_kwargs):
        return self._child("column")

    def row(self, **_kwargs):
        return self._child("row")

    def box(self, **_kwargs):
        return self._child("box")

    def split(self, **_kwargs):
        return self._child("split")

    def grid_flow(self, **_kwargs):
        return self._child("grid_flow")

    def column_flow(self, **_kwargs):
        return self._child("column_flow")

    def _child(self, kind):
        child = Layout(kind, self)
        self.children.append(child)
        return child

    # leaves --------------------------------------------------------------
    def label(self, text="", icon="", **_kwargs):
        node = self._child("label")
        node.text = text
        node.icon = icon

    def prop(self, _owner, name, text="", slider=False, **_kwargs):
        node = self._child("prop")
        node.name = name
        node.text = text
        node.slider = slider

    def operator(self, idname, text="", icon="", depress=False, emboss=True, **_kwargs):
        node = self._child("operator")
        node.name = idname
        node.text = text or idname
        node.icon = icon
        node.depress = bool(depress)
        node.emboss = emboss
        return SimpleNamespace()

    def popover(self, panel="", text="", icon="", **_kwargs):
        node = self._child("popover")
        node.panel = panel
        node.text = text
        node.icon = icon

    def template_icon(self, icon_value=0, scale=1.0, **_kwargs):
        node = self._child("icon")
        node.text = f"icon#{icon_value}"
        node.slider = scale

    def template_list(self, *args, **_kwargs):
        node = self._child("template_list")
        node.name = str(args[0]) if args else ""

    def progress(self, factor=0.0, type="BAR", text="", **_kwargs):
        node = self._child("progress")
        node.text = text
        node.slider = factor

    def separator(self, factor=1.0, **_kwargs):
        self._child("separator")

    # unknown API: record it instead of silently losing it
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def fallback(*args, **_kwargs):
            node = self._child(name)
            node.text = str(args[0]) if args else ""
            return node

        return fallback


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def height(node):
    """Approximate the vertical cost of a node in layout lines."""
    if node.kind in LEAF_KINDS:
        return 1.0
    if node.kind == "separator":
        return SEPARATOR_PX / LINE_PX
    if node.kind in ("row", "split", "grid_flow", "column_flow"):
        return max((height(child) for child in node.children), default=0.0)
    total = sum(height(child) for child in node.children)
    if node.kind == "box":
        total += BOX_PX / LINE_PX
    return total


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def row_widgets(row):
    return [child for child in row.children if child.kind in LEAF_KINDS]


def stats(root):
    nodes = list(walk(root))
    widgets = [node for node in nodes if node.kind in LEAF_KINDS]
    rows = [node for node in nodes if node.kind in ("row", "split")]
    widest = max(rows, key=lambda row: len(row_widgets(row)), default=None)
    operators = [node for node in widgets if node.kind == "operator"]
    emphasized = [node for node in operators if node.scale_y >= EMPHASIS]
    notes = [node for node in widgets if node.kind == "label" and node.icon in NOTE_ICONS]
    boxes = [node for node in nodes if node.kind == "box"]
    popovers = [node for node in widgets if node.kind == "popover"]
    keys = [(node.name, node.text) for node in operators]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    lines = height(root)
    return {
        "lines": lines,
        "px": int(round(lines * LINE_PX)),
        "widgets": len(widgets),
        "operators": len(operators),
        "emphasized": len(emphasized),
        "notes": len(notes),
        "boxes": len(boxes),
        "popovers": len(popovers),
        "widest": len(row_widgets(widest)) if widest is not None else 0,
        "widest_row": widest,
        "duplicates": duplicates,
    }


def tree_lines(node, depth=0, out=None):
    if out is None:
        out = []
    pad = "  " * depth
    if node.kind in LEAF_KINDS:
        detail = node.text or node.name
        if node.kind == "operator" and node.scale_y >= 1.2:
            detail = f"{detail}  [emphasis {node.scale_y:g}]"
        if node.kind == "popover":
            detail = f"{node.panel or 'popover'} {detail}".strip()
        out.append(f"{pad}{node.kind}: {detail}")
    elif node.kind == "separator":
        out.append(f"{pad}—")
    else:
        out.append(f"{pad}{node.kind}")
        for child in node.children:
            tree_lines(child, depth + 1, out)
    return out


# --------------------------------------------------------------------------
# Scenario setup
# --------------------------------------------------------------------------


def make_cube():
    mesh = bpy.data.meshes.new("CensusCube")
    mesh.from_pydata(
        [
            (-1, -1, -1),
            (1, -1, -1),
            (1, 1, -1),
            (-1, 1, -1),
            (-1, -1, 1),
            (1, -1, 1),
            (1, 1, 1),
            (-1, 1, 1),
        ],
        [],
        [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    )
    mesh.update()
    obj = bpy.data.objects.new("Scan_014", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def reset(context):
    wm = context.window_manager
    session = wm.remi_session
    session.active = False
    session.busy = False
    session.interactive = False
    session.automatic = False
    session.flow_completed = 0
    session.flow_total = 0
    session.object_name = ""
    session.status = ""
    session.current_step = "Source"
    session.step_index = 0
    session.can_undo = False
    session.can_redo = False
    session.source_faces = 0
    session.current_faces = 0
    session.current_vertices = 0
    session.checkpoint_megabytes = 0.0
    session.stage = "REPAIR"
    wm.remi_ui.workspace = "OPTIMIZE"
    wm.remi_generation.busy = False
    wm.remi_generation.mesh_busy = False
    wm.remi_generation.status = ""
    wm.remi_generation.progress = 0.0
    wm.remi_image.busy = False
    wm.remi_image.status = ""
    wm.remi_image.progress = 0.0
    wm.remi_texture.busy = False
    wm.remi_texture.status = ""
    context.scene.remi_instant_meshes.session_active = False


def start_session(context, stage, step=None):
    session = context.window_manager.remi_session
    session.active = True
    session.object_name = "Scan_014"
    session.current_vertices = 48213
    session.current_faces = 96240
    session.source_faces = 1240500
    session.current_step = step or stage.title()
    session.status = "Volumetric closing applied"
    session.step_index = 2
    session.can_undo = True
    session.can_redo = False
    session.checkpoint_megabytes = 84.6
    session.stage = stage


def cases():
    """(label, setup(context) -> None) in rough order of user journey."""
    return [
        ("board job · busy", _generate_busy),
        ("texture · no mesh", _texture_no_mesh),
        ("texture · mesh", _texture_mesh),
        ("optimize · idle", _optimize_idle),
        ("session · repair", lambda ctx: start_session(ctx, "REPAIR", "Repair")),
        ("session · remesh", lambda ctx: start_session(ctx, "REMESH", "Remesh")),
        ("session · retopology", lambda ctx: start_session(ctx, "RETOPOLOGY", "Retopology")),
        ("session · uv", lambda ctx: start_session(ctx, "UV", "UV")),
        ("session · bake", lambda ctx: start_session(ctx, "BAKE", "Bake")),
        ("session · busy", _session_busy),
        ("session · interactive repair", _interactive_repair),
        ("session · interactive retopo", _interactive_retopo),
        ("session · automatic", _session_automatic),
    ]


def _generate_busy(ctx):
    ctx.window_manager.remi_ui.workspace = "OPTIMIZE"
    state = ctx.window_manager.remi_image
    state.busy = True
    state.status = "Generating image…"
    state.progress = 0.42


def _texture_no_mesh(ctx):
    ctx.window_manager.remi_ui.workspace = "TEXTURE"
    ctx.view_layer.objects.active = None


def _texture_mesh(ctx):
    ctx.window_manager.remi_ui.workspace = "TEXTURE"
    obj = bpy.data.objects.get("Scan_014")
    if obj is not None:
        ctx.view_layer.objects.active = obj


def _optimize_idle(ctx):
    ctx.window_manager.remi_ui.workspace = "OPTIMIZE"
    obj = bpy.data.objects.get("Scan_014")
    if obj is not None:
        ctx.view_layer.objects.active = obj


def _session_busy(ctx):
    start_session(ctx, "REMESH", "Remesh")
    state = ctx.window_manager.remi_session
    state.busy = True
    state.status = "Remeshing at 0.010 m…"
    state.can_undo = False


def _interactive_repair(ctx):
    start_session(ctx, "REPAIR", "Manual Repair")
    ctx.window_manager.remi_session.interactive = True


def _interactive_retopo(ctx):
    start_session(ctx, "RETOPOLOGY", "Instant Meshes")
    state = ctx.window_manager.remi_session
    state.interactive = True
    settings = ctx.scene.remi_instant_meshes
    settings.session_active = True
    settings.status = "Solving fields"
    settings.progress = 0.35
    runtime = sys.modules["remi.features.retopology.instant_meshes.runtime"].runtime
    if runtime.session is None:
        runtime.session = SimpleNamespace(active=False)


def _session_automatic(ctx):
    start_session(ctx, "REPAIR", "Full Flow")
    state = ctx.window_manager.remi_session
    state.automatic = True
    state.flow_total = 4
    state.flow_completed = 2


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def load_package():
    if "remi" in sys.modules:
        return sys.modules["remi"]
    spec = importlib.util.spec_from_file_location(
        "remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = module
    spec.loader.exec_module(module)
    return module


def render_report(results, blender_version):
    ok = {label: data for label, data in results.items() if "error" not in data}
    failed = {label: data for label, data in results.items() if "error" in data}
    widths = [("State", 30), ("Lines", 6), ("~px", 6), ("Widgets", 8), ("Widest", 7),
              ("Emph", 5), ("Notes", 6), ("Boxes", 6), ("Pop", 4), ("Dupes", 6)]
    table = ["# Remi sidebar census — before the shell redesign", ""]
    table.append(f"Blender {blender_version}. Approximate: 20 px per line, 8 px per box, 6 px per separator.")
    table.append("Generated by `scripts/ui_census.py`; no add-on code was modified.")
    table.append("")
    table.append("| " + " | ".join(name.ljust(width) for name, width in widths) + " |")
    table.append("|" + "|".join("-" * (width + 2) for _, width in widths) + "|")
    for label, data in sorted(ok.items(), key=lambda item: item[1]["px"], reverse=True):
        cells = [
            label,
            f"{data['lines']:.1f}",
            str(data["px"]),
            str(data["widgets"]),
            str(data["widest"]),
            str(data["emphasized"]),
            str(data["notes"]),
            str(data["boxes"]),
            str(data["popovers"]),
            str(len(data["duplicates"])),
        ]
        table.append("| " + " | ".join(value.ljust(width) for value, (_, width) in zip(cells, widths)) + " |")
    table.append("")
    table.append("`Widest` is the most widgets squeezed into one row (2 is already tight at 320 px).")
    table.append("`Emph` counts buttons at `scale_y >= 1.25` — competing primary actions.")
    table.append("`Dupes` counts the same operator id with the same label twice in one state.")
    table.append("")
    for label, data in failed.items():
        table.append(f"> `{label}` cannot be drawn without a live session: {data['error']}")
    if failed:
        table.append("")
    table.append("## Slowest states")
    table.append("")
    for label, data in sorted(ok.items(), key=lambda item: item[1]["px"], reverse=True)[:3]:
        table.append(f"### {label} — {data['px']} px")
        table.append("")
        if data["duplicates"]:
            table.append("Duplicated operators: " + ", ".join(f"`{name}`" for name in data["duplicates"]))
            table.append("")
        table.append("```")
        table.extend(tree_lines(data["root"]))
        table.append("```")
        table.append("")
    table.append("## All states")
    table.append("")
    for label, data in sorted(ok.items(), key=lambda item: item[1]["px"], reverse=True):
        table.append(f"### {label} — {data['lines']:.1f} lines / {data['px']} px")
        table.append("")
        table.append("```")
        table.extend(tree_lines(data["root"]))
        table.append("```")
        table.append("")
    return "\n".join(table) + "\n"


def main():
    module = load_package()
    cube = make_cube()
    context = bpy.context
    if context.view_layer is None:
        context.scene.view_layers[0]

    results = {}
    module.register()
    module_panel = sys.modules["remi.app.ui.main_panel"].Remi_PT_MainPanel
    try:
        for label, setup in cases():
            reset(context)
            setup(context)
            recorder = Layout("root")
            panel = SimpleNamespace(layout=recorder)
            try:
                module_panel.draw(panel, context)
            except Exception as exc:  # a state only a live session can produce
                results[label] = {"error": f"{type(exc).__name__}: {exc}", "root": recorder}
                continue
            data = stats(recorder)
            data["root"] = recorder
            results[label] = data
    finally:
        reset(context)
        if cube.name in bpy.data.objects:
            bpy.data.objects.remove(cube, do_unlink=True)
        module.unregister()

    report = render_report(results, bpy.app.version_string)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report, encoding="utf-8")

    print("\n" + report.split("## Slowest states")[0])
    print(f"Report written to {REPORT}")
    print("REMI_UI_CENSUS_OK")


if __name__ == "__main__":
    main()
