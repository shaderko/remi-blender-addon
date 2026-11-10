"""Multi-variant Remi sidebar shells — a throwaway design playground.

    blender --factory-startup --python scripts/ui_sketch.py

Registers extra panels under the "UI" sidebar category (press N in the 3D
viewport, then the UI tab):

    Control   demo/live mode, scenario, stage, reload
    A         tiered panel — one compact stack, stage picked from a menu
    B         stage list — every stage a row, only the current one expanded
    C         compact rail — nine rows; the whole stage lives in a sheet
    D         checklist — six rows; stage settings open in a modal dialog

Nothing in this file is imported by the add-on. It draws real feature controls
(borrowed from features/*/feature.py) against demo state, or against the real
session when MODE is LIVE. Buttons are inert in demo mode.

Delete this file once a shell has been chosen and ported.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import bpy
from bpy.types import Menu, Operator, Panel

MODULE_NAME = "remi_ui_sketch"
BOOT = {"source": "not loaded", "error": ""}


def _script_location():
    raw = globals().get("__file__")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    return path if path.is_file() else None


FILE = _script_location()
LAST_MTIME = FILE.stat().st_mtime if FILE is not None else 0.0

MODE = "DEMO"  # DEMO | LIVE
SCENARIO = "session"  # idle | session | busy | interactive | automatic
STAGE = "REPAIR"
WORKSPACE = "OPTIMIZE"
AUTO_RELOAD = False  # opt-in: no background machinery until you ask for it

SHORT_SCENARIOS = (
    ("idle", "Idle"),
    ("session", "Stage"),
    ("busy", "Busy"),
    ("interactive", "Intr"),
    ("automatic", "Auto"),
)

SCENARIO_COPY = {
    "idle": "entry view — no session",
    "session": "session running, stage selected",
    "busy": "stage executing",
    "interactive": "interactive tool owns the viewport",
    "automatic": "full flow, stage 3 of 4",
}


# --------------------------------------------------------------------------
# Package loading
# --------------------------------------------------------------------------


def remi():
    return sys.modules["remi"]


def locate_root():
    override = os.environ.get("REMI_ROOT")
    if override:
        root = Path(override).resolve()
        if (root / "__init__.py").is_file():
            return root
    if FILE is not None:
        root = FILE.parents[1]
        if (root / "__init__.py").is_file() and (root / "app").is_dir():
            return root
    raise RuntimeError(
        "Cannot find the Remi checkout. Launch Blender with "
        "--python <repo>/scripts/ui_sketch.py, or set REMI_ROOT."
    )


def registered_remi():
    """An enabled copy of Remi in this Blender, if any."""
    for key in list(bpy.context.preferences.addons.keys()):
        name = key.lower()
        if name != "remi" and not name.endswith(".remi"):
            continue
        module = sys.modules.get(key)
        if module is not None and hasattr(module, "app"):
            return module
    return None


def load_remi():
    checkout = sys.modules.get("remi")
    if checkout is not None and hasattr(checkout, "app"):
        return "checkout"
    installed = registered_remi()
    if installed is not None:
        sys.modules["remi"] = installed
        return f"installed · {installed.__name__}"
    root = locate_root()
    spec = importlib.util.spec_from_file_location(
        "remi", root / "__init__.py", submodule_search_locations=[str(root)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = module
    spec.loader.exec_module(module)
    module.register()
    return "checkout"


def boot():
    """Never raise: panels stay visible and report the problem instead."""
    try:
        BOOT["source"] = load_remi()
        BOOT["error"] = ""
    except Exception as exc:
        BOOT["source"] = "not loaded"
        BOOT["error"] = f"{type(exc).__name__}: {exc}"
        sys.modules.pop("remi", None)
    return BOOT


def features():
    return remi().app.application.get_application().features


def stage_items():
    return [(feature.descriptor.id, feature.descriptor.name) for feature in features()]


def stage_name(identifier):
    try:
        return dict(stage_items()).get(identifier, identifier.title())
    except Exception:
        return identifier.title()


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


def demo_state():
    state = SimpleNamespace(
        active=SCENARIO != "idle",
        busy=False,
        interactive=False,
        automatic=False,
        flow_total=0,
        flow_completed=0,
        object_name="Scan_014",
        status="Ready",
        current_step="Repair",
        step_index=2,
        can_undo=True,
        can_redo=False,
        source_faces=1_240_500,
        current_faces=96_240,
        current_vertices=48_213,
        checkpoint_megabytes=84.6,
        stage=STAGE,
    )
    if SCENARIO == "busy":
        state.busy = True
        state.can_undo = False
        state.status = "Remeshing at 0.010 m…"
    elif SCENARIO == "interactive":
        state.interactive = True
        state.status = "Manual repair: draw around one hole"
    elif SCENARIO == "automatic":
        state.automatic = True
        state.flow_total = 4
        state.flow_completed = 2
        state.status = "Full flow · stage 3 of 4"
    return state


def current_state(context):
    if MODE == "LIVE":
        return context.window_manager.remi_session
    return demo_state()


def state_for(context, stage):
    """A state whose stage is forced — used by the sheet and dialog surfaces."""
    state = current_state(context)
    if stage and state.stage != stage:
        state = SimpleNamespace(**{**vars(state), "stage": stage})
    return state


def redraw(context):
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        area.tag_redraw()


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------


class SK_OT_Noop(Operator):
    """Inert stand-in so demo buttons draw without touching real state."""

    bl_idname = "remi_sketch.noop"
    bl_label = "Sketch Button"
    bl_options = {"INTERNAL"}

    command: bpy.props.StringProperty(default="")
    stage: bpy.props.StringProperty(default="")

    def execute(self, _context):
        return {"CANCELLED"}


class SK_OT_Stage(Operator):
    """Pick the stage used by the demo shells."""

    bl_idname = "remi_sketch.stage"
    bl_label = "Sketch Stage"
    bl_options = {"INTERNAL"}

    stage: bpy.props.StringProperty(default="REPAIR")

    def execute(self, context):
        global STAGE
        STAGE = self.stage
        redraw(context)
        return {"FINISHED"}


class SK_OT_Config(Operator):
    """Switch demo/live mode or the scenario being previewed."""

    bl_idname = "remi_sketch.config"
    bl_label = "Sketch Config"
    bl_options = {"INTERNAL"}

    mode: bpy.props.StringProperty(default="")
    scenario: bpy.props.StringProperty(default="")
    workspace: bpy.props.StringProperty(default="")

    def execute(self, context):
        global MODE, SCENARIO, WORKSPACE
        if self.mode:
            MODE = self.mode
        if self.scenario:
            SCENARIO = self.scenario
        if self.workspace:
            WORKSPACE = self.workspace
        redraw(context)
        return {"FINISHED"}


class SK_OT_Reload(Operator):
    """Re-execute this file after editing it."""

    bl_idname = "remi_sketch.reload"
    bl_label = "Reload Sketch"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        reload_sketch()
        redraw(context)
        return {"FINISHED"}


class SK_OT_ToggleAutoReload(Operator):
    bl_idname = "remi_sketch.auto_reload"
    bl_label = "Toggle Auto Reload"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        global AUTO_RELOAD
        AUTO_RELOAD = not AUTO_RELOAD
        if AUTO_RELOAD:
            arm_watch()
        else:
            disarm_watch()
        redraw(context)
        return {"FINISHED"}


class SK_OT_StageDialog(Operator):
    """Stage settings in a modal dialog (shell D)."""

    bl_idname = "remi_sketch.dialog"
    bl_label = "Stage Settings"
    bl_options = {"INTERNAL"}

    stage: bpy.props.StringProperty(default="REPAIR")

    def invoke(self, context, _event):
        global STAGE
        if self.stage:
            STAGE = self.stage
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        state = state_for(context, self.stage)
        leaves(self.layout, context, state)

    def execute(self, context):
        redraw(context)
        return {"FINISHED"}


# --------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------


def op(layout, live_id, text="", icon="NONE", depress=False):
    """Real operator in LIVE mode, inert stand-in in DEMO mode."""
    identifier = live_id if MODE == "LIVE" else "remi_sketch.noop"
    return layout.operator(identifier, text=text, icon=icon, depress=depress)


def session_button(layout, command, text, icon="NONE", depress=False):
    button = op(layout, "remi.session_command", text, icon, depress)
    button.command = command
    return button


def stage_button(layout, identifier, text=None, depress=False):
    button = op(layout, "remi.session_stage", text or stage_name(identifier), depress=depress)
    button.stage = identifier
    return button


def leaves(layout, context, state):
    """Borrow the production feature controls — never retyped here."""
    feature = features().get(state.stage)
    ui_context = remi().workflow.contracts.FeatureUIContext(
        blender_context=context, state=state
    )
    feature.draw(layout, ui_context)


def workspace_tabs(layout, context):
    row = layout.row(align=True)
    row.scale_y = 1.1
    live = MODE == "LIVE"
    for identifier, label in (
        ("OPTIMIZE", "Optimize"),
        ("TEXTURE", "Texture"),
    ):
        selected = (
            remi().app.ui.workspace.active_workspace(context) == identifier
            if live
            else WORKSPACE == identifier
        )
        button = op(row, "remi.select_workspace", label, depress=selected)
        if live:
            button.workspace = identifier
        else:
            button.command = identifier


def header(layout, state):
    row = layout.row(align=True)
    row.label(text=state.object_name or "No mesh", icon="OBJECT_DATA")
    if state.current_faces:
        row.label(text=f"{state.current_faces:,} faces")
        if state.source_faces:
            row.label(text=f"{state.current_faces / state.source_faces:.0%}")


def status_line(layout, state):
    icon = "TIME" if state.busy else "INFO"
    layout.label(text=state.status or "Ready", icon=icon)


def history(layout, state):
    row = layout.row(align=True)
    back = row.row(align=True)
    back.enabled = state.can_undo and not state.busy
    session_button(back, "UNDO", "Back", "TRIA_LEFT")
    redo = row.row(align=True)
    redo.enabled = state.can_redo and not state.busy
    session_button(redo, "REDO", "Redo", "TRIA_RIGHT")
    reset = row.row(align=True)
    reset.enabled = state.step_index > 0 and not state.busy
    session_button(reset, "RESET", "Start", "FILE_REFRESH")

    finish = layout.row(align=True)
    finish.enabled = not state.busy and not state.interactive
    finish.scale_y = 1.3
    session_button(finish, "FINISH", "Finish", "CHECKMARK")
    session_button(finish, "CANCEL", "Cancel", "X")


def presets(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.prop(context.scene.remi_flow, "preset", text="")
    run = box.column()
    run.scale_y = 1.2
    op(run, "remi.run_full_flow", "Run Full Flow", "PLAY")


def stage_menu(layout, context, state):
    layout.menu("SK_MT_stages", text=f"Stage · {stage_name(state.stage)}")


def draw_entry(layout, context):
    """The pre-session view. Shared by every shell; the variants differ below."""
    workspace_tabs(layout, context)
    obj = context.view_layer.objects.active
    row = layout.row(align=True)
    if obj is not None and obj.type == "MESH":
        row.label(text=obj.name, icon="OBJECT_DATA")
        row.label(text=f"{len(obj.data.polygons):,} faces")
    else:
        row.label(text="Select one mesh", icon="INFO")
    start = layout.column()
    start.scale_y = 1.5
    op(start, "remi.start_session", "Start Remi", "PLAY")
    presets(layout, context)


# --------------------------------------------------------------------------
# Shell A — tiered panel
# --------------------------------------------------------------------------


def shell_a(layout, context, state):
    workspace_tabs(layout, context)
    header(layout, state)
    status_line(layout, state)
    stage_menu(layout, context, state)
    layout.separator()
    box = layout.box()
    box.label(text=f"{stage_name(state.stage)} settings", icon="MOD_REMESH")
    leaves(box, context, state)
    layout.separator()
    history(layout, state)
    presets(layout, context)


# --------------------------------------------------------------------------
# Shell B — stage list
# --------------------------------------------------------------------------


def shell_b(layout, context, state):
    workspace_tabs(layout, context)
    header(layout, state)
    status_line(layout, state)
    for identifier, name in stage_items():
        current = identifier == state.stage
        box = layout.box()
        row = box.row(align=True)
        if current:
            row.label(text=name, icon="TRIA_DOWN")
            row.label(text="current")
        else:
            stage_button(row, identifier, text=name)
        if current:
            leaves(box, context, state)
    layout.separator()
    history(layout, state)


# --------------------------------------------------------------------------
# Shell C — compact rail, stage in a sheet
# --------------------------------------------------------------------------


def shell_c(layout, context, state):
    workspace_tabs(layout, context)
    header(layout, state)
    status_line(layout, state)
    stage_menu(layout, context, state)
    layout.popover(panel="SK_PT_StageSheet", text="Open Stage Sheet", icon="PREFERENCES")
    # The rail keeps one primary action. Its duplicate inside the sheet is the
    # reason features need a draw_settings/draw_actions split (design finding).
    primary = layout.column()
    primary.scale_y = 1.4
    session_button(primary, stage_primary_command(state.stage), "Run " + stage_name(state.stage))
    layout.separator()
    history(layout, state)


def stage_primary_command(identifier):
    return {
        "REPAIR": "REPAIR",
        "REMESH": "REMESH",
        "RETOPOLOGY": "INSTANT_START",
        "UV": "UV",
        "BAKE": "BAKE_ALL",
    }.get(identifier, identifier)


# --------------------------------------------------------------------------
# Shell D — checklist, stage in a dialog
# --------------------------------------------------------------------------


def shell_d(layout, context, state):
    workspace_tabs(layout, context)
    header(layout, state)
    for identifier, name in stage_items():
        row = layout.row(align=True)
        icon = "RADIOBUT_ON" if identifier == state.stage else "RADIOBUT_OFF"
        row.label(text=name, icon=icon)
        button = op(row, "remi_sketch.dialog", "Open")
        button.stage = identifier
    layout.separator()
    history(layout, state)


# --------------------------------------------------------------------------
# Menus and popover surfaces
# --------------------------------------------------------------------------


class SK_MT_Stages(Menu):
    bl_idname = "SK_MT_stages"
    bl_label = "Stage"

    def draw(self, _context):
        layout = self.layout
        try:
            for identifier, name in stage_items():
                stage_button(layout, identifier, depress=identifier == STAGE)
        except Exception as exc:
            layout.label(text=f"{type(exc).__name__}: {exc}", icon="ERROR")


class SK_PT_StageSheet(Panel):
    """The floating stage sheet for shell C."""

    bl_label = "Stage"
    bl_idname = "SK_PT_StageSheet"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 20

    def draw(self, context):
        layout = self.layout
        try:
            state = current_state(context)
            layout.use_property_split = False
            row = layout.row(align=True)
            for identifier, name in stage_items():
                stage_button(row, identifier, depress=identifier == state.stage)
            layout.separator()
            leaves(layout, context, state)
        except Exception as exc:
            layout.label(text=f"{type(exc).__name__}: {exc}", icon="ERROR")


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------


def guarded(draw):
    def wrapper(panel, context):
        layout = panel.layout
        try:
            draw(layout, context)
        except Exception as exc:  # keep iterating instead of losing the panel
            layout.label(text=f"{type(exc).__name__}: {exc}", icon="ERROR")

    return wrapper


class SK_PT_Control(Panel):
    bl_label = "Sketch Control"
    bl_idname = "SK_PT_Control"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UI"

    def draw(self, context):
        layout = self.layout
        source = BOOT["source"]
        loaded = source != "not loaded"
        layout.label(text=f"Remi · {source}", icon="CHECKMARK" if loaded else "ERROR")
        if BOOT["error"]:
            for line in textwrap.wrap(BOOT["error"], 42):
                layout.label(text=line, icon="ERROR")
            layout.label(text="Launch: blender --python scripts/ui_sketch.py", icon="INFO")
        layout.separator()
        row = layout.row(align=True)
        for key, label in (("DEMO", "Demo"), ("LIVE", "Live")):
            button = layout.operator("remi_sketch.config", text=label, depress=MODE == key)
            button.mode = key
        layout.label(text=f"{MODE} · {SCENARIO_COPY.get(SCENARIO, SCENARIO)}")
        row = layout.row(align=True)
        for key, label in SHORT_SCENARIOS:
            button = row.operator("remi_sketch.config", text=label, depress=SCENARIO == key)
            button.scenario = key
        layout.menu("SK_MT_stages", text=f"Demo stage · {stage_name(STAGE)}")
        layout.separator()
        row = layout.row(align=True)
        row.operator("remi_sketch.reload", text="Reload", icon="FILE_REFRESH")
        row.operator(
            "remi_sketch.auto_reload",
            text="Auto ✓" if AUTO_RELOAD else "Auto ✗",
        )
        layout.label(text="Expand two shells to compare.", icon="INFO")


class SK_PT_A(Panel):
    bl_label = "A · Tiered panel"
    bl_idname = "SK_PT_A"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UI"
    draw = guarded(lambda layout, context: draw_variant(layout, context, shell_a))


class SK_PT_B(Panel):
    bl_label = "B · Stage list"
    bl_idname = "SK_PT_B"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UI"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(lambda layout, context: draw_variant(layout, context, shell_b))


class SK_PT_C(Panel):
    bl_label = "C · Compact rail"
    bl_idname = "SK_PT_C"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UI"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(lambda layout, context: draw_variant(layout, context, shell_c))


class SK_PT_D(Panel):
    bl_label = "D · Checklist"
    bl_idname = "SK_PT_D"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UI"
    bl_options = {"DEFAULT_CLOSED"}
    draw = guarded(lambda layout, context: draw_variant(layout, context, shell_d))


def draw_automatic(layout, context, state):
    """Full-flow mode is the same in every shell today — mirror it for fairness."""
    workspace_tabs(layout, context)
    header(layout, state)
    layout.label(
        text=f"Full Flow · {state.flow_completed}/{state.flow_total} stages complete"
    )
    action = layout.column()
    action.scale_y = 1.2
    op(action, "remi.stop_full_flow", "Stop After Current Stage", "PAUSE")
    layout.label(text="Each stage may keep Blender busy", icon="INFO")


def draw_variant(layout, context, shell):
    state = current_state(context)
    if not state.active:
        draw_entry(layout, context)
        return
    if state.automatic:
        draw_automatic(layout, context, state)
        return
    shell(layout, context, state)


# --------------------------------------------------------------------------
# Reload loop
# --------------------------------------------------------------------------


def reload_sketch():
    if FILE is None:
        return
    old = sys.modules.get(MODULE_NAME)
    saved = (MODE, SCENARIO, STAGE, WORKSPACE, AUTO_RELOAD)
    if old is not None:
        try:
            old.unregister()
        except Exception:
            pass
    spec = importlib.util.spec_from_file_location(MODULE_NAME, FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    module.MODE, module.SCENARIO, module.STAGE, module.WORKSPACE, module.AUTO_RELOAD = saved
    module.register()
    module.boot()
    print("Remi sketch: reloaded")


def watch():
    module = sys.modules.get(MODULE_NAME)
    if module is None or FILE is None or not getattr(module, "AUTO_RELOAD", False):
        disarm_watch()
        return None
    try:
        mtime = FILE.stat().st_mtime
    except OSError:
        return 1.0
    if mtime > module.LAST_MTIME + 0.01:
        module.LAST_MTIME = mtime
        try:
            module.reload_sketch()
        except Exception as exc:
            print(f"Remi sketch: reload failed: {exc}")
        return 0.5
    return 0.7


def arm_watch():
    """Start the file watcher. Opt-in: nothing runs in the background otherwise."""
    existing = getattr(bpy, "_remi_sketch_watch_fn", None)
    if existing is not None and bpy.app.timers.is_registered(existing):
        return
    bpy._remi_sketch_watch_fn = watch
    bpy.app.timers.register(watch, first_interval=1.0)


def disarm_watch():
    existing = getattr(bpy, "_remi_sketch_watch_fn", None)
    if existing is not None and bpy.app.timers.is_registered(existing):
        bpy.app.timers.unregister(existing)
    bpy._remi_sketch_watch_fn = None


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

CLASSES = (
    SK_OT_Noop,
    SK_OT_Stage,
    SK_OT_Config,
    SK_OT_Reload,
    SK_OT_ToggleAutoReload,
    SK_OT_StageDialog,
    SK_MT_Stages,
    SK_PT_StageSheet,
    SK_PT_Control,
    SK_PT_A,
    SK_PT_B,
    SK_PT_C,
    SK_PT_D,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def main():
    register()
    boot()
    if FILE is not None and AUTO_RELOAD:
        arm_watch()
    print(
        "Remi sketch ready: N-panel → UI tab → Sketch Control. "
        f"Remi source: {BOOT['source']}"
    )
    if BOOT["error"]:
        print(f"Remi sketch could not load Remi: {BOOT['error']}")
    if FILE is not None:
        print(f"Click Reload after editing {FILE}.")


if __name__ == "__main__":
    main()
