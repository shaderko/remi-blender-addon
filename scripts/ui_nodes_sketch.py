"""Node-graph surface for image generation — throwaway prototype.

    scripts\\run-ui-sketch.bat ui_nodes_sketch.py

Registers a custom node tree shown in a real Node Editor area:

    Prompt  ──┐
              ├──▶ Generate ──▶ Image
    Image  ───┘  (context)

The tree is a plain datablock (fake user). Nothing is added to the scene: no
objects, no image planes, no materials. Nodes carry image references, not scene
content.

Known limitation, stated in the node itself: Iris-3B is text-to-image only, so
the Context socket is wired for lineage but does not condition the model yet.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
from pathlib import Path

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Menu, Node, NodeTree, Operator, Panel

BOOT = {"source": "not loaded", "error": ""}
TREE_ID = "RemiImageGraph"
GRAPH_NAME = "Remi Image Graph"


def _script_location():
    raw = globals().get("__file__")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve() if path.is_file() else None


FILE = _script_location()


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
    raise RuntimeError("Cannot find the Remi checkout; set REMI_ROOT or launch with --python.")


def registered_remi():
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
    try:
        BOOT["source"] = load_remi()
        BOOT["error"] = ""
    except Exception as exc:
        BOOT["source"] = "not loaded"
        BOOT["error"] = f"{type(exc).__name__}: {exc}"
        sys.modules.pop("remi", None)


# --------------------------------------------------------------------------
# Graph storage and editor binding
# --------------------------------------------------------------------------


def graph(create=False):
    tree = bpy.data.node_groups.get(GRAPH_NAME)
    if tree is None and create:
        tree = bpy.data.node_groups.new(GRAPH_NAME, TREE_ID)
        tree.use_fake_user = True
        seed_graph(tree)
    return tree

def seed_graph(tree):
    prompt = tree.nodes.new("RemiNodePrompt")
    prompt.location = (-320, 80)
    generate = tree.nodes.new("RemiNodeGenerate")
    generate.location = (40, 40)
    tree.links.new(prompt.outputs["Prompt"], generate.inputs["Prompt"])
    tree.nodes.active = generate


def bind_editor(space, tree):
    space.tree_type = TREE_ID
    space.node_tree = tree
    if hasattr(space, "show_region_ui"):
        space.show_region_ui = True


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


class RemiImageGraph(NodeTree):
    """The graph datablock. Data only — nothing is added to the scene."""

    bl_idname = TREE_ID
    bl_label = "Remi Image Graph"
    bl_icon = "NODETREE"


class RemiNodePrompt(Node):
    """Reusable text and settings."""

    bl_idname = "RemiNodePrompt"
    bl_label = "Prompt"
    bl_icon = "TEXT"

    text: StringProperty(name="Prompt", default="")
    negative: StringProperty(name="Negative", default="")
    seed: IntProperty(name="Seed", default=0, min=0)

    def init(self, context):
        self.outputs.new("NodeSocketString", "Prompt")

    def draw_buttons(self, context, layout):
        layout.prop(self, "text", text="")
        row = layout.row(align=True)
        row.prop(self, "seed")
        layout.prop(self, "negative", text="")


class RemiNodeImage(Node):
    """An existing image on disk, as a data reference — never scene content."""

    bl_idname = "RemiNodeImage"
    bl_label = "Image"
    bl_icon = "IMAGE_DATA"

    path: StringProperty(name="Path", subtype="FILE_PATH", default="")

    def init(self, context):
        self.outputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        layout.prop(self, "path", text="")
        preview = preview_icon(self.path)
        if preview:
            layout.template_icon(icon_value=preview, scale=2.2)
        else:
            layout.label(text="No preview", icon="INFO")


def preview_icon(path):
    try:
        return sys.modules["remi.features.images.gallery"].gallery.icon(path)
    except Exception:
        return 0


class RemiNodeGenerate(Node):
    """One image generation. Prompt comes from a link or from the node itself."""

    bl_idname = "RemiNodeGenerate"
    bl_label = "Generate"
    bl_icon = "RENDER_STILL"

    prompt: StringProperty(name="Prompt", default="")
    context_note: StringProperty(default="")

    def init(self, context):
        self.inputs.new("NodeSocketString", "Prompt")
        self.inputs.new("NodeSocketImage", "Context")
        self.outputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        settings = context.scene.remi_image
        layout.prop(settings, "model", text="")
        row = layout.row(align=True)
        row.prop(settings, "size", text="")
        row.prop(settings, "count", text="")
        prompt_input = self.inputs["Prompt"]
        if not prompt_input.is_linked:
            layout.prop(self, "prompt", text="")
        state = context.window_manager.remi_image
        if state.busy:
            layout.progress(factor=float(state.progress), type="BAR", text=f"{int(state.progress * 100)}%")
        else:
            button = layout.operator("remi_nodes.generate", text="Generate", icon="PLAY")
            button.node = self.name
        if self.inputs["Context"].is_linked:
            layout.label(text="Context is lineage only — model takes text", icon="INFO")


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------


def resolved_prompt(node):
    input_socket = node.inputs["Prompt"]
    if input_socket.is_linked:
        source = input_socket.links[0].from_node
        if source.bl_idname == "RemiNodePrompt":
            return source.text
    return node.prompt


class RemiNodes_OT_OpenGraph(Operator):
    """Open (or create) the Remi image graph in a Node Editor area."""

    bl_idname = "remi_nodes.open_graph"
    bl_label = "Open Image Graph"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type in {"VIEW_3D", "NODE_EDITOR"}

    def execute(self, context):
        try:
            tree = graph(create=True)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not create the graph: {exc}")
            return {"CANCELLED"}
        area = context.area
        window = context.window
        if area is None or window is None:
            self.report({"WARNING"}, "Open this from a 3D viewport or Node Editor")
            return {"CANCELLED"}
        if area.type == "NODE_EDITOR":
            bind_editor(area.spaces.active, tree)
            return {"FINISHED"}
        if area.width < 400:
            self.report({"WARNING"}, "Widen the editor before opening the graph")
            return {"CANCELLED"}
        screen = window.screen
        before = {item.as_pointer() for item in screen.areas}
        new_area = None
        try:
            with context.temp_override(window=window, screen=screen, area=area):
                bpy.ops.screen.area_split(direction="VERTICAL", factor=0.55)
            new_area = next(
                (item for item in screen.areas if item.as_pointer() not in before), None
            )
        except Exception as exc:
            self.report({"WARNING"}, f"Split failed ({exc}); using this area instead")
        target = new_area or area
        target.type = "NODE_EDITOR"
        bind_editor(target.spaces.active, tree)
        self.report({"INFO"}, "Remi image graph opened")
        return {"FINISHED"}


class RemiNodes_OT_Generate(Operator):
    """Run one generation for a Generate node."""

    bl_idname = "remi_nodes.generate"
    bl_label = "Generate From Node"
    bl_options = {"REGISTER"}

    node: StringProperty()

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_image", None)
        return state is not None and not state.busy

    def execute(self, context):
        tree = graph()
        if tree is None or self.node not in tree.nodes:
            self.report({"ERROR"}, "Node not found")
            return {"CANCELLED"}
        node = tree.nodes[self.node]
        prompt = resolved_prompt(node).strip()
        if not prompt:
            self.report({"ERROR"}, "Prompt is empty")
            return {"CANCELLED"}
        context.scene.remi_image.prompt = prompt
        result = bpy.ops.remi.generate_image()
        if "FINISHED" not in result:
            return {"CANCELLED"}
        self.report({"INFO"}, "Generation started from the graph")
        return {"FINISHED"}


class RemiNodes_OT_AddNode(Operator):
    bl_idname = "remi_nodes.add"
    bl_label = "Add Node"
    bl_options = {"REGISTER"}

    node_type: StringProperty(default="RemiNodeGenerate")

    def execute(self, context):
        tree = graph(create=True)
        node = tree.nodes.new(self.node_type)
        tree.nodes.active = node
        return {"FINISHED"}


class RemiNodes_MT_Add(Menu):
    bl_idname = "RemiNodes_MT_Add"
    bl_label = "Remi Nodes"

    def draw(self, context):
        layout = self.layout
        for identifier, label, icon in (
            ("RemiNodePrompt", "Prompt", "TEXT"),
            ("RemiNodeImage", "Image", "IMAGE_DATA"),
            ("RemiNodeGenerate", "Generate", "RENDER_STILL"),
        ):
            button = layout.operator("remi_nodes.add", text=label, icon=icon)
            button.node_type = identifier


class RemiNodes_PT_Panel(Panel):
    bl_label = "Image graph"
    bl_idname = "RemiNodes_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GEN"

    def draw(self, _context):
        layout = self.layout
        layout.label(
            text=f"Remi · {BOOT['source']}",
            icon="CHECKMARK" if BOOT["source"] != "not loaded" else "ERROR",
        )
        if BOOT["error"]:
            for line in textwrap.wrap(BOOT["error"], 42):
                layout.label(text=line, icon="ERROR")
        layout.operator("remi_nodes.open_graph", text="Open Image Graph", icon="NODE")
        layout.menu("RemiNodes_MT_Add", text="Add Node", icon="ADD")
        tree = graph()
        if tree is not None:
            layout.label(text=f"{len(tree.nodes)} nodes", icon="NODETREE")


CLASSES = (
    RemiImageGraph,
    RemiNodePrompt,
    RemiNodeImage,
    RemiNodeGenerate,
    RemiNodes_OT_OpenGraph,
    RemiNodes_OT_Generate,
    RemiNodes_OT_AddNode,
    RemiNodes_MT_Add,
    RemiNodes_PT_Panel,
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
    print(f"Remi node sketch ready: N-panel → GEN → Open Image Graph. Remi source: {BOOT['source']}")


if __name__ == "__main__":
    main()
