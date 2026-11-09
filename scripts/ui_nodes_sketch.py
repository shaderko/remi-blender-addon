"""Wired node graph for the Remi pipeline — throwaway prototype.

    scripts\\run-ui-sketch.bat

A custom NodeTree displayed in a real Node Editor. Nodes carry the pipeline's
state and call the add-on's existing operators; nothing here replaces the
add-on UI, and nothing is added to the scene except the results the pipeline
itself creates (splat object, mesh).

    Prompt ──▶ Generate ──▶ Image ──▶ Splat 3D ──▶ Mesh
                 (image)              (splats)     (mesh)

Long jobs report through the add-on's own state (`remi_image`, `remi_generation`);
a timer polls until a job finishes and then binds the result to the node.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
import time
from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
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
# Remi plumbing
# --------------------------------------------------------------------------


def gallery():
    return sys.modules["remi.features.images.gallery"].gallery


def image_state(context):
    return context.window_manager.remi_image


def generation_state(context):
    return context.window_manager.remi_generation


def entries(context):
    settings = context.scene.remi_image
    return gallery().refresh(settings.output, image_state(context).refresh_token)


def preview_icon(path):
    try:
        return gallery().icon(path) if path else 0
    except Exception:
        return 0


def splat_for(path):
    try:
        return sys.modules["remi.blender.gaussian_splats"].splat_for_image(path)
    except Exception:
        return None


def mesh_for(splat):
    try:
        return sys.modules["remi.blender.reconstructed_mesh"].mesh_for_splat(splat)
    except Exception:
        return None


def newest_since(context, stamp):
    for entry in entries(context):
        if entry.created >= stamp - 2.0:
            return entry
    return None


def redraw():
    try:
        for area in bpy.context.screen.areas:
            area.tag_redraw()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Graph storage
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
    prompt.location = (-360, 120)
    generate = tree.nodes.new("RemiNodeGenerate")
    generate.location = (-40, 80)
    image = tree.nodes.new("RemiNodeImage")
    image.location = (260, 80)
    splat = tree.nodes.new("RemiNodeSplat")
    splat.location = (560, 80)
    tree.links.new(prompt.outputs["Prompt"], generate.inputs["Prompt"])
    tree.links.new(generate.outputs["Image"], image.inputs["Image"])
    tree.links.new(image.outputs["Image"], splat.inputs["Image"])
    tree.nodes.active = generate


def bind_editor(space, tree):
    space.tree_type = TREE_ID
    space.node_tree = tree
    if hasattr(space, "show_region_ui"):
        space.show_region_ui = True
    return space.node_tree == tree


def upstream_node(node, socket_name):
    socket = node.inputs.get(socket_name)
    if socket is None or not socket.is_linked:
        return None
    return socket.links[0].from_node


def upstream_image_path(node):
    source = upstream_node(node, "Image")
    while source is not None:
        path = getattr(source, "result_path", "") or getattr(source, "path", "")
        if path:
            return path
        source = upstream_node(source, "Image")
    return ""


def upstream_prompt(node):
    source = upstream_node(node, "Prompt")
    if source is not None and getattr(source, "text", ""):
        return source.text
    return getattr(node, "prompt", "")


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


class RemiImageGraph(NodeTree):
    bl_idname = TREE_ID
    bl_label = "Remi Image Graph"
    bl_icon = "NODETREE"


class RemiNodePrompt(Node):
    """Reusable prompt text."""

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
        layout.prop(self, "negative", text="")
        row = layout.row(align=True)
        row.prop(self, "seed")
        button = row.operator("remi_nodes.use_seed", text="", icon="FILE_REFRESH")
        button.node = self.name


class RemiNodeGenerate(Node):
    """One image generation through remi.generate_image."""

    bl_idname = "RemiNodeGenerate"
    bl_label = "Generate Image"
    bl_icon = "RENDER_STILL"

    prompt: StringProperty(name="Prompt", default="")
    result_path: StringProperty(name="Result", default="", subtype="FILE_PATH")
    pending: BoolProperty(default=False)
    started: FloatProperty(default=0.0)
    message: StringProperty(default="")

    def init(self, context):
        self.inputs.new("NodeSocketString", "Prompt")
        self.outputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        settings = context.scene.remi_image
        state = image_state(context)
        layout.prop(settings, "model", text="")
        row = layout.row(align=True)
        row.prop(settings, "size", text="")
        row.prop(settings, "count", text="")
        if not self.inputs["Prompt"].is_linked:
            layout.prop(self, "prompt", text="")
        if self.pending or state.busy:
            layout.progress(
                factor=float(state.progress),
                type="BAR",
                text=state.status or "Generating…",
            )
            return
        button = layout.operator("remi_nodes.generate", text="Generate", icon="PLAY")
        button.node = self.name
        if self.message:
            layout.label(text=self.message, icon="INFO")
        if self.result_path:
            icon = preview_icon(self.result_path)
            if icon:
                layout.template_icon(icon_value=icon, scale=1.6)
            layout.label(text=Path(self.result_path).name)


class RemiNodeImage(Node):
    """A concrete image, passed to the next stage."""

    bl_idname = "RemiNodeImage"
    bl_label = "Image"
    bl_icon = "IMAGE_DATA"

    path: StringProperty(name="Path", default="", subtype="FILE_PATH")
    result_path: StringProperty(default="")

    def init(self, context):
        self.inputs.new("NodeSocketImage", "Image")
        self.outputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        linked = self.inputs["Image"].is_linked
        effective = upstream_image_path(self) if linked else self.path
        if linked:
            layout.label(text="From graph", icon="LINKED")
        else:
            layout.prop(self, "path", text="")
            row = layout.row(align=True)
            row.operator("remi_nodes.use_latest", text="Latest").node = self.name
            row.operator("remi_nodes.use_selected", text="Selected").node = self.name
        icon = preview_icon(effective)
        if icon:
            layout.template_icon(icon_value=icon, scale=2.0)
        elif effective:
            layout.label(text="No preview", icon="INFO")


class RemiNodeSplat(Node):
    """Image → Gaussian splats through remi.generate_splats."""

    bl_idname = "RemiNodeSplat"
    bl_label = "3D Splats"
    bl_icon = "OUTLINER_OB_POINTCLOUD"

    result_path: StringProperty(default="")
    object_name: StringProperty(default="")
    pending: BoolProperty(default=False)
    started: FloatProperty(default=0.0)
    message: StringProperty(default="")

    def init(self, context):
        self.inputs.new("NodeSocketImage", "Image")
        self.outputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        state = generation_state(context)
        source = upstream_image_path(self)
        if not source:
            layout.label(text="Connect an image", icon="INFO")
            return
        layout.label(text=Path(source).name)
        if self.pending or state.busy:
            layout.progress(factor=float(state.progress), type="BAR", text=state.status or "3D…")
            return
        button = layout.operator("remi_nodes.splat", text="Make 3D", icon="PLAY")
        button.node = self.name
        if self.message:
            layout.label(text=self.message, icon="INFO")
        if self.object_name and self.object_name in bpy.data.objects:
            row = layout.row(align=True)
            row.label(text=self.object_name, icon="CHECKMARK")
            row.operator("remi_nodes.focus", text="", icon="RESTRICT_SELECT_OFF").object_name = self.object_name
            mesh = mesh_for(splat_for(source)) if source else None
            if mesh is not None:
                layout.label(text=f"Mesh · {mesh.name}", icon="MOD_REMESH")


class RemiNodeMesh(Node):
    """Splats → mesh through remi.create_mesh."""

    bl_idname = "RemiNodeMesh"
    bl_label = "Mesh"
    bl_icon = "MOD_REMESH"

    object_name: StringProperty(default="")
    pending: BoolProperty(default=False)
    message: StringProperty(default="")

    def init(self, context):
        self.inputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        state = generation_state(context)
        source = upstream_image_path(self)
        splat = splat_for(source) if source else None
        if splat is None:
            layout.label(text="No splats upstream", icon="INFO")
            return
        mesh = mesh_for(splat)
        if mesh is not None:
            row = layout.row(align=True)
            row.label(text=mesh.name, icon="CHECKMARK")
            row.operator("remi_nodes.focus", text="", icon="RESTRICT_SELECT_OFF").object_name = mesh.name
        if self.pending or state.mesh_busy:
            layout.progress(factor=0.5, type="BAR", text=state.mesh_status or "Building mesh…")
            return
        if mesh is None:
            button = layout.operator("remi_nodes.mesh", text="Create Mesh", icon="PLAY")
            button.node = self.name
        if self.message:
            layout.label(text=self.message, icon="INFO")


# --------------------------------------------------------------------------
# Result polling
# --------------------------------------------------------------------------


def pending_nodes():
    tree = graph()
    if tree is None:
        return []
    return [node for node in tree.nodes if getattr(node, "pending", False)]


def poll_results():
    tree = graph()
    if tree is None:
        return None
    waiting = pending_nodes()
    if not waiting:
        return None
    changed = False
    image = image_state(bpy.context)
    generation = generation_state(bpy.context)
    for node in waiting:
        if node.bl_idname == "RemiNodeGenerate":
            if image.busy:
                continue
            entry = newest_since(bpy.context, node.started)
            node.pending = False
            if entry is not None:
                node.result_path = str(entry.path)
                node.message = f"done · seed {entry.seed}"
            else:
                node.message = image.status or "No result — check the console"
            changed = True
        elif node.bl_idname == "RemiNodeSplat":
            if generation.busy:
                continue
            node.pending = False
            source = upstream_image_path(node)
            splat = splat_for(source) if source else None
            if splat is not None:
                node.object_name = splat.name
                node.message = "splats ready"
            else:
                node.message = generation.status or "No splats — check the console"
            changed = True
        elif node.bl_idname == "RemiNodeMesh":
            if generation.mesh_busy:
                continue
            node.pending = False
            source = upstream_image_path(node)
            splat = splat_for(source) if source else None
            mesh = mesh_for(splat) if splat is not None else None
            node.message = f"mesh · {mesh.name}" if mesh is not None else (generation.mesh_status or "No mesh")
            changed = True
    if changed:
        redraw()
    return 0.5 if any(getattr(node, "pending", False) for node in tree.nodes) else None


def arm_poll():
    if not bpy.app.timers.is_registered(poll_results):
        bpy.app.timers.register(poll_results, first_interval=0.5)


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------


def node_of(name):
    tree = graph()
    if tree is None or name not in tree.nodes:
        return None
    return tree.nodes[name]


class RemiNodes_OT_Generate(Operator):
    """Start an image generation from a Generate node."""

    bl_idname = "remi_nodes.generate"
    bl_label = "Generate From Node"
    bl_options = {"REGISTER"}

    node: StringProperty()

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_image", None)
        return state is not None and not state.busy

    def execute(self, context):
        node = node_of(self.node)
        if node is None:
            self.report({"ERROR"}, "Node not found")
            return {"CANCELLED"}
        prompt = upstream_prompt(node).strip()
        if not prompt:
            self.report({"ERROR"}, "Connect a Prompt node or type a prompt")
            return {"CANCELLED"}
        context.scene.remi_image.prompt = prompt
        node.pending = True
        node.started = time.time()
        node.message = ""
        result = bpy.ops.remi.generate_image()
        if "FINISHED" not in result:
            node.pending = False
            return {"CANCELLED"}
        arm_poll()
        self.report({"INFO"}, "Generating from the graph")
        return {"FINISHED"}


class RemiNodes_OT_Splat(Operator):
    """Generate Gaussian splats from the upstream image."""

    bl_idname = "remi_nodes.splat"
    bl_label = "Make Splats From Node"
    bl_options = {"REGISTER"}

    node: StringProperty()

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_generation", None)
        return state is not None and not state.busy and not state.mesh_busy

    def execute(self, context):
        node = node_of(self.node)
        if node is None:
            self.report({"ERROR"}, "Node not found")
            return {"CANCELLED"}
        source = upstream_image_path(node)
        if not source or not Path(bpy.path.abspath(source)).is_file():
            self.report({"ERROR"}, "No image upstream")
            return {"CANCELLED"}
        node.pending = True
        node.started = time.time()
        node.message = ""
        result = bpy.ops.remi.generate_splats(image_path=source)
        if "FINISHED" not in result:
            node.pending = False
            return {"CANCELLED"}
        arm_poll()
        self.report({"INFO"}, "Generating splats from the graph")
        return {"FINISHED"}


class RemiNodes_OT_Mesh(Operator):
    """Create a mesh from the upstream splats."""

    bl_idname = "remi_nodes.mesh"
    bl_label = "Create Mesh From Node"
    bl_options = {"REGISTER"}

    node: StringProperty()

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_generation", None)
        return state is not None and not state.busy and not state.mesh_busy

    def execute(self, context):
        node = node_of(self.node)
        if node is None:
            self.report({"ERROR"}, "Node not found")
            return {"CANCELLED"}
        source = upstream_image_path(node)
        splat = splat_for(source) if source else None
        if splat is None:
            self.report({"ERROR"}, "No splats upstream")
            return {"CANCELLED"}
        for obj in context.view_layer.objects:
            obj.select_set(False)
        splat.select_set(True)
        context.view_layer.objects.active = splat
        node.pending = True
        node.message = ""
        result = bpy.ops.remi.create_mesh()
        if "FINISHED" not in result:
            node.pending = False
            return {"CANCELLED"}
        arm_poll()
        return {"FINISHED"}


class RemiNodes_OT_UseLatest(Operator):
    bl_idname = "remi_nodes.use_latest"
    bl_label = "Use Latest Image"
    bl_options = {"REGISTER"}

    node: StringProperty()

    def execute(self, context):
        node = node_of(self.node)
        items = entries(context)
        if node is None or not items:
            return {"CANCELLED"}
        node.path = str(items[0].path)
        redraw()
        return {"FINISHED"}


class RemiNodes_OT_UseSelected(Operator):
    bl_idname = "remi_nodes.use_selected"
    bl_label = "Use Selected Image"
    bl_options = {"REGISTER"}

    node: StringProperty()

    def execute(self, context):
        node = node_of(self.node)
        selected = image_state(context).selected
        if node is None or not selected:
            return {"CANCELLED"}
        node.path = bpy.path.abspath(selected)
        redraw()
        return {"FINISHED"}


class RemiNodes_OT_UseSeed(Operator):
    bl_idname = "remi_nodes.use_seed"
    bl_label = "Randomise Seed"
    bl_options = {"REGISTER"}

    node: StringProperty()

    def execute(self, context):
        node = node_of(self.node)
        if node is None:
            return {"CANCELLED"}
        node.seed = int(time.time()) % 2_147_483_647
        redraw()
        return {"FINISHED"}


class RemiNodes_OT_Focus(Operator):
    bl_idname = "remi_nodes.focus"
    bl_label = "Select Object"
    bl_options = {"REGISTER"}

    object_name: StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            return {"CANCELLED"}
        for other in context.view_layer.objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


# --------------------------------------------------------------------------
# Editor binding
# --------------------------------------------------------------------------


class RemiNodes_OT_OpenGraph(Operator):
    """Open the image graph in a Node Editor area."""

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
            self.report({"WARNING"}, "Open from a 3D viewport or Node Editor")
            return {"CANCELLED"}
        if area.type == "NODE_EDITOR":
            if not bind_editor(area.spaces.active, tree):
                self.report({"WARNING"}, "This editor refused the graph")
            return {"FINISHED"}
        if area.width < 400:
            self.report({"WARNING"}, "Widen the editor first")
            return {"CANCELLED"}
        screen = window.screen
        before = {item.as_pointer() for item in screen.areas}
        target = None
        try:
            with context.temp_override(window=window, screen=screen, area=area):
                bpy.ops.screen.area_split(direction="VERTICAL", factor=0.55)
            target = next((item for item in screen.areas if item.as_pointer() not in before), None)
        except Exception as exc:
            self.report({"WARNING"}, f"Split failed ({exc}); using this area")
        target = target or area
        target.type = "NODE_EDITOR"
        if not bind_editor(target.spaces.active, tree):
            self.report({"ERROR"}, "Could not bind the graph to the editor")
            return {"CANCELLED"}
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
        redraw()
        return {"FINISHED"}


class RemiNodes_MT_Add(Menu):
    bl_idname = "RemiNodes_MT_Add"
    bl_label = "Remi Nodes"

    def draw(self, context):
        layout = self.layout
        for identifier, label, icon in (
            ("RemiNodePrompt", "Prompt", "TEXT"),
            ("RemiNodeImage", "Image", "IMAGE_DATA"),
            ("RemiNodeGenerate", "Generate Image", "RENDER_STILL"),
            ("RemiNodeSplat", "3D Splats", "OUTLINER_OB_POINTCLOUD"),
            ("RemiNodeMesh", "Mesh", "MOD_REMESH"),
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
    RemiNodeSplat,
    RemiNodeMesh,
    RemiNodes_OT_OpenGraph,
    RemiNodes_OT_Generate,
    RemiNodes_OT_Splat,
    RemiNodes_OT_Mesh,
    RemiNodes_OT_UseLatest,
    RemiNodes_OT_UseSelected,
    RemiNodes_OT_UseSeed,
    RemiNodes_OT_Focus,
    RemiNodes_OT_AddNode,
    RemiNodes_MT_Add,
    RemiNodes_PT_Panel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    if bpy.app.timers.is_registered(poll_results):
        bpy.app.timers.unregister(poll_results)
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
