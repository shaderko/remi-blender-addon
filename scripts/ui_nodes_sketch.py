"""Wired node graph for the Remi pipeline — throwaway prototype.

    scripts\\run-ui-sketch.bat

Prompt ──▶ Generate Image ──▶ [Image card]
             (appends a card, never overwrites the previous one)

Image ──▶ Generate 3D ──▶ [Splats card]
            (object pinned to the card)

Splats ──▶ Mesh

Cards pin their own artifact: a path for images, an object name for splats.
Re-running a producer appends another card instead of mutating a shared slot, so
the graph is a board while Remi's runtime slots stay single.
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
CARD_SCALE = 3.0
CARD_WIDTH = 320.0
NODE_ITEMS = (
    ("RemiNodePrompt", "Prompt", "TEXT"),
    ("RemiNodeMerge", "Merge Text", "SORTALPHA"),
    ("RemiNodeImage", "Image card", "IMAGE_DATA"),
    ("RemiNodeGenerate", "Generate Image", "RENDER_STILL"),
    ("RemiNodeSplat", "Generate 3D", "OUTLINER_OB_POINTCLOUD"),
    ("RemiNodeSplats", "Splats card", "OUTLINER_OB_POINTCLOUD"),
    ("RemiNodeMesh", "Mesh", "MOD_REMESH"),
)


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


def redraw():
    try:
        for area in bpy.context.screen.areas:
            area.tag_redraw()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Text resolution: sockets carry values by walking back through the graph
# --------------------------------------------------------------------------


def output_text(socket):
    node = socket.node
    name = socket.name
    if node.bl_idname == "RemiNodePrompt":
        return node.slot_value(name)
    if node.bl_idname == "RemiNodeMerge":
        parts = [input_text(node, "Text 1"), input_text(node, "Text 2")]
        return node.separator.join(part for part in parts if part.strip())
    if node.bl_idname == "RemiNodeGenerate":
        return node.prompt
    return ""


def input_text(node, socket_name):
    socket = node.inputs.get(socket_name)
    if socket is None:
        return ""
    if socket.is_linked:
        return output_text(socket.links[0].from_socket)
    return ""


def input_image_path(node, socket_name="Image"):
    socket = node.inputs.get(socket_name)
    if socket is None or not socket.is_linked:
        return ""
    return image_path_of(socket.links[0].from_node)


def image_path_of(node):
    if node.bl_idname == "RemiNodeImage":
        return node.path or input_image_path(node)
    if node.bl_idname == "RemiNodeSplats":
        return node.image_path or input_image_path(node)
    if node.bl_idname == "RemiNodeGenerate":
        return getattr(node, "result_path", "")
    if node.bl_idname == "RemiNodeSplat":
        return input_image_path(node)
    return getattr(node, "path", "")


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
    prompt.location = (-420, 160)
    merge = tree.nodes.new("RemiNodeMerge")
    merge.location = (-160, 200)
    generate = tree.nodes.new("RemiNodeGenerate")
    generate.location = (140, 140)
    tree.links.new(prompt.outputs["Text 1"], merge.inputs["Text 1"])
    tree.links.new(merge.outputs["Text"], generate.inputs["Prompt"])
    tree.nodes.active = generate


def normalize_cards(tree):
    """Widen existing image cards so their preview is not clipped."""
    for node in tree.nodes:
        if node.bl_idname == "RemiNodeImage" and node.width < CARD_WIDTH:
            node.width = CARD_WIDTH


def bind_editor(space, tree):
    normalize_cards(tree)
    space.tree_type = TREE_ID
    space.node_tree = tree
    if hasattr(space, "show_region_ui"):
        space.show_region_ui = True
    return space.node_tree == tree


def spawn_image_card(tree, source_node, path, index=0, prompt="", seed=0, model=""):
    card = tree.nodes.new("RemiNodeImage")
    card.location = (source_node.location.x + CARD_WIDTH + 80, source_node.location.y - (CARD_WIDTH * 0.75) * index)
    card.width = CARD_WIDTH
    card.path = path
    card.prompt = prompt
    card.seed = seed
    card.model = model
    tree.links.new(source_node.outputs["Image"], card.inputs["Image"])
    return card


def spawn_splat_card(tree, source_node, object_name, image_path, index=0):
    card = tree.nodes.new("RemiNodeSplats")
    card.location = (source_node.location.x + 420, source_node.location.y - 260 * index)
    card.width = 300.0
    card.object_name = object_name
    card.image_path = image_path
    tree.links.new(source_node.outputs["Image"], card.inputs["Image"])
    return card


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


class RemiImageGraph(NodeTree):
    bl_idname = TREE_ID
    bl_label = "Remi Image Graph"
    bl_icon = "NODETREE"


class RemiNodePrompt(Node):
    """Two text slots; each is an input and an output, so prompts compose."""

    bl_idname = "RemiNodePrompt"
    bl_label = "Prompt"
    bl_icon = "TEXT"

    text1: StringProperty(name="Text 1", default="")
    text2: StringProperty(name="Text 2", default="")

    def init(self, context):
        self.inputs.new("NodeSocketString", "Text 1")
        self.inputs.new("NodeSocketString", "Text 2")
        self.outputs.new("NodeSocketString", "Text 1")
        self.outputs.new("NodeSocketString", "Text 2")

    def slot_value(self, name):
        linked = input_text(self, name)
        if linked:
            return linked
        return self.text1 if name == "Text 1" else self.text2

    def draw_buttons(self, context, layout):
        for name, prop in (("Text 1", "text1"), ("Text 2", "text2")):
            if self.inputs[name].is_linked:
                layout.label(text=f"{name}: from link", icon="LINKED")
            else:
                layout.prop(self, prop, text="")
        layout.label(text="two slots, both exposed", icon="INFO")


class RemiNodeMerge(Node):
    """Combine two text streams."""

    bl_idname = "RemiNodeMerge"
    bl_label = "Merge Text"
    bl_icon = "SORTALPHA"

    separator: StringProperty(name="Separator", default=", ")

    def init(self, context):
        self.inputs.new("NodeSocketString", "Text 1")
        self.inputs.new("NodeSocketString", "Text 2")
        self.outputs.new("NodeSocketString", "Text")

    def draw_buttons(self, context, layout):
        layout.prop(self, "separator", text="")
        preview = self.separator.join(
            part for part in (input_text(self, "Text 1"), input_text(self, "Text 2")) if part.strip()
        )
        if preview:
            for line in textwrap.wrap(preview, 30)[:2]:
                layout.label(text=line)


class RemiNodeGenerate(Node):
    """Image generation. Appends an Image card on success."""

    bl_idname = "RemiNodeGenerate"
    bl_label = "Generate Image"
    bl_icon = "RENDER_STILL"

    prompt: StringProperty(name="Prompt", default="")
    negative: StringProperty(name="Negative", default="")
    pending: BoolProperty(default=False)
    started: FloatProperty(default=0.0)
    message: StringProperty(default="")
    runs: IntProperty(default=0)
    result_path: StringProperty(default="", subtype="FILE_PATH")

    def init(self, context):
        self.inputs.new("NodeSocketString", "Prompt")
        self.inputs.new("NodeSocketString", "Negative")
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
        if not self.inputs["Negative"].is_linked and settings.model == "base":
            layout.prop(self, "negative", text="")
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
        if self.runs:
            layout.label(text=f"{self.runs} image card(s) made", icon="IMAGE_DATA")


class RemiNodeImage(Node):
    """A board card: one concrete image with the settings that produced it."""

    bl_idname = "RemiNodeImage"
    bl_label = "Image"
    bl_icon = "IMAGE_DATA"

    path: StringProperty(name="Path", default="", subtype="FILE_PATH")
    prompt: StringProperty(default="")
    seed: IntProperty(default=0)
    model: StringProperty(default="")

    def init(self, context):
        self.inputs.new("NodeSocketImage", "Image")
        self.outputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        pinned = bool(self.path)
        effective = self.path or input_image_path(self)
        if not pinned and not effective:
            layout.label(text="Empty card", icon="INFO")
            row = layout.row(align=True)
            row.operator("remi_nodes.use_latest", text="Latest", icon="FILE_REFRESH").node = self.name
            layout.label(text="Previews are 32 px icons in a node — use Open", icon="INFO")
            return
        if not pinned:
            layout.label(text="Follows the graph", icon="LINKED")
        icon = preview_icon(effective)
        if icon:
            layout.template_icon(icon_value=icon, scale=CARD_SCALE)
        if self.prompt:
            for line in textwrap.wrap(self.prompt, 38)[:2]:
                layout.label(text=line)
        bits = [Path(effective).name]
        if self.seed:
            bits.append(f"seed {self.seed}")
        if self.model:
            bits.append(self.model)
        layout.label(text=" · ".join(bits))
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("remi_nodes.card_open", text="Open Full Size", icon="ZOOM_IN").node = self.name
        if not pinned:
            row.operator("remi_nodes.card_pin", text="", icon="PINNED").node = self.name


class RemiNodeSplat(Node):
    """Image → Gaussian splats. Appends a Splats card on success."""

    bl_idname = "RemiNodeSplat"
    bl_label = "Generate 3D"
    bl_icon = "OUTLINER_OB_POINTCLOUD"

    pending: BoolProperty(default=False)
    started: FloatProperty(default=0.0)
    message: StringProperty(default="")
    runs: IntProperty(default=0)

    def init(self, context):
        self.inputs.new("NodeSocketImage", "Image")
        self.outputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        state = generation_state(context)
        source = input_image_path(self)
        if not source:
            layout.label(text="Connect an image", icon="INFO")
            return
        layout.label(text=Path(source).name)
        if self.pending or state.busy:
            layout.progress(factor=float(state.progress), type="BAR", text=state.status or "3D…")
            return
        button = layout.operator("remi_nodes.splat", text="Generate Splats", icon="PLAY")
        button.node = self.name
        if self.message:
            layout.label(text=self.message, icon="INFO")
        if self.runs:
            layout.label(text=f"{self.runs} splats card(s) made", icon="OUTLINER_OB_POINTCLOUD")


class RemiNodeSplats(Node):
    """A board card for one splat object, pinned by name."""

    bl_idname = "RemiNodeSplats"
    bl_label = "Splats"
    bl_icon = "OUTLINER_OB_POINTCLOUD"

    object_name: StringProperty(default="")
    image_path: StringProperty(default="", subtype="FILE_PATH")

    def init(self, context):
        self.inputs.new("NodeSocketImage", "Image")
        self.outputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        obj = bpy.data.objects.get(self.object_name) if self.object_name else None
        if obj is None:
            layout.label(text="Missing object", icon="ERROR")
            return
        row = layout.row(align=True)
        row.label(text=obj.name, icon="CHECKMARK")
        row.operator("remi_nodes.focus", text="", icon="RESTRICT_SELECT_OFF").object_name = obj.name
        if self.image_path:
            icon = preview_icon(self.image_path)
            if icon:
                layout.template_icon(icon_value=icon, scale=1.6)
        mesh = mesh_for(obj)
        if mesh is not None:
            layout.label(text=f"mesh · {mesh.name}", icon="MOD_REMESH")
        layout.label(text=self.object_name)


class RemiNodeMesh(Node):
    """Splats → mesh."""

    bl_idname = "RemiNodeMesh"
    bl_label = "Mesh"
    bl_icon = "MOD_REMESH"

    pending: BoolProperty(default=False)
    message: StringProperty(default="")

    def init(self, context):
        self.inputs.new("NodeSocketImage", "Image")

    def draw_buttons(self, context, layout):
        state = generation_state(context)
        source = input_image_path(self)
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
# Result polling: bind the job's artifact to the node, append a card
# --------------------------------------------------------------------------


def all_nodes():
    tree = graph()
    return list(tree.nodes) if tree else []


def poll_results():
    tree = graph()
    if tree is None:
        return None
    waiting = [node for node in tree.nodes if getattr(node, "pending", False)]
    if not waiting:
        return None
    context = bpy.context
    image = image_state(context)
    generation = generation_state(context)
    changed = False
    for node in waiting:
        if node.bl_idname == "RemiNodeGenerate" and not image.busy:
            node.pending = False
            entry = next(
                (item for item in entries(context) if item.created >= node.started - 2.0), None
            )
            if entry is not None:
                node.runs += 1
                node.result_path = str(entry.path)
                node.message = f"done · seed {entry.seed}"
                spawn_image_card(
                    tree,
                    node,
                    str(entry.path),
                    node.runs - 1,
                    prompt=(input_text(node, "Prompt") or node.prompt),
                    seed=entry.seed,
                    model=entry.model_label,
                )
            else:
                node.message = image.status or "No result — check the Info bar"
            changed = True
        elif node.bl_idname == "RemiNodeSplat" and not generation.busy:
            node.pending = False
            source = input_image_path(node)
            splat = splat_for(source) if source else None
            if splat is not None:
                node.runs += 1
                node.message = "splats ready"
                spawn_splat_card(tree, node, splat.name, source, node.runs - 1)
            else:
                node.message = generation.status or "No splats — check the Info bar"
            changed = True
        elif node.bl_idname == "RemiNodeMesh" and not generation.mesh_busy:
            node.pending = False
            source = input_image_path(node)
            splat = splat_for(source) if source else None
            mesh = mesh_for(splat) if splat is not None else None
            node.message = f"mesh · {mesh.name}" if mesh is not None else (
                generation.mesh_status or "No mesh"
            )
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
        prompt = (input_text(node, "Prompt") or node.prompt).strip()
        if not prompt:
            node.message = "No prompt — link a Prompt node or type one"
            redraw()
            return {"CANCELLED"}
        settings = context.scene.remi_image
        settings.prompt = prompt
        negative = input_text(node, "Negative") or node.negative
        if negative and settings.model == "base":
            settings.negative_prompt = negative
        if not bpy.ops.remi.generate_image.poll():
            node.message = "Blocked — a session or another job is running"
            redraw()
            return {"CANCELLED"}
        node.pending = True
        node.started = time.time()
        node.message = ""
        try:
            result = bpy.ops.remi.generate_image("INVOKE_DEFAULT")
        except Exception as exc:
            node.pending = False
            node.message = f"{type(exc).__name__}: {exc}"
            redraw()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not ({"FINISHED", "RUNNING_MODAL"} & set(result)):
            node.pending = False
            node.message = image_state(context).status or "Refused — check the Info bar"
            redraw()
            return {"CANCELLED"}
        arm_poll()
        redraw()
        return {"FINISHED"}


class RemiNodes_OT_Splat(Operator):
    """Generate Gaussian splats from the upstream image."""

    bl_idname = "remi_nodes.splat"
    bl_label = "Generate Splats From Node"
    bl_options = {"REGISTER"}

    node: StringProperty()

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_generation", None)
        return state is not None and not state.busy and not state.mesh_busy

    def execute(self, context):
        node = node_of(self.node)
        if node is None:
            return {"CANCELLED"}
        source = input_image_path(node)
        if not source or not Path(bpy.path.abspath(source)).is_file():
            node.message = "No image upstream"
            redraw()
            return {"CANCELLED"}
        if not bpy.ops.remi.generate_splats.poll():
            node.message = "Blocked — another job is running"
            redraw()
            return {"CANCELLED"}
        node.pending = True
        node.started = time.time()
        node.message = ""
        try:
            result = bpy.ops.remi.generate_splats("INVOKE_DEFAULT", image_path=source)
        except Exception as exc:
            node.pending = False
            node.message = f"{type(exc).__name__}: {exc}"
            redraw()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not ({"FINISHED", "RUNNING_MODAL"} & set(result)):
            node.pending = False
            node.message = generation_state(context).status or "Refused — check the Info bar"
            redraw()
            return {"CANCELLED"}
        arm_poll()
        redraw()
        return {"FINISHED"}


class RemiNodes_OT_Mesh(Operator):
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
            return {"CANCELLED"}
        source = input_image_path(node)
        splat = splat_for(source) if source else None
        if splat is None:
            node.message = "No splats upstream"
            redraw()
            return {"CANCELLED"}
        if not bpy.ops.remi.create_mesh.poll():
            node.message = "Blocked — another job is running"
            redraw()
            return {"CANCELLED"}
        for obj in context.view_layer.objects:
            obj.select_set(False)
        splat.select_set(True)
        context.view_layer.objects.active = splat
        node.pending = True
        node.message = ""
        try:
            result = bpy.ops.remi.create_mesh("INVOKE_DEFAULT")
        except Exception as exc:
            node.pending = False
            node.message = f"{type(exc).__name__}: {exc}"
            redraw()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not ({"FINISHED", "RUNNING_MODAL"} & set(result)):
            node.pending = False
            node.message = generation_state(context).mesh_status or "Refused — check the Info bar"
            redraw()
            return {"CANCELLED"}
        arm_poll()
        redraw()
        return {"FINISHED"}


class RemiNodes_OT_CardPin(Operator):
    """Pin a card to the image currently shown."""

    bl_idname = "remi_nodes.card_pin"
    bl_label = "Pin Image"
    bl_options = {"REGISTER"}

    node: StringProperty()

    def execute(self, context):
        node = node_of(self.node)
        if node is None or node.bl_idname != "RemiNodeImage":
            return {"CANCELLED"}
        effective = node.path or input_image_path(node)
        if not effective:
            return {"CANCELLED"}
        node.path = bpy.path.abspath(effective)
        redraw()
        return {"FINISHED"}


class RemiNodes_OT_CardOpen(Operator):
    bl_idname = "remi_nodes.card_open"
    bl_label = "Open Image"
    bl_options = {"REGISTER"}

    node: StringProperty()

    def execute(self, context):
        node = node_of(self.node)
        if node is None:
            return {"CANCELLED"}
        path = node.path or input_image_path(node)
        if not path:
            return {"CANCELLED"}
        opener = getattr(bpy.ops.gen, "open_full", None)
        if opener is None:
            self.report({"WARNING"}, "Load ui_generate_sketch.py for the full-size viewer")
            return {"CANCELLED"}
        return opener(path=bpy.path.abspath(path))


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


class RemiNodes_OT_SyncBoard(Operator):
    """Add a pinned card for every image already in the gallery."""

    bl_idname = "remi_nodes.sync_board"
    bl_label = "Sync Board From Gallery"
    bl_options = {"REGISTER"}

    def execute(self, context):
        tree = graph(create=True)
        pinned = {node.path for node in tree.nodes if node.bl_idname == "RemiNodeImage" and node.path}
        added = 0
        for index, entry in enumerate(entries(context)):
            if str(entry.path) in pinned:
                continue
            card = tree.nodes.new("RemiNodeImage")
            card.location = (-420, -200 - 340 * index)
            card.width = CARD_WIDTH
            card.path = str(entry.path)
            card.prompt = entry.prompt
            card.seed = entry.seed
            card.model = entry.model_label
            added += 1
        redraw()
        self.report({"INFO"}, f"Added {added} card(s)")
        return {"FINISHED"}


class RemiNodes_OT_AddNode(Operator):
    bl_idname = "remi_nodes.add"
    bl_label = "Add Node"
    bl_options = {"REGISTER"}

    node_type: StringProperty(default="RemiNodeGenerate")

    def execute(self, context):
        tree = graph(create=True)
        node = tree.nodes.new(self.node_type)
        node.location = (0, -300)
        if self.node_type == "RemiNodeImage":
            node.width = CARD_WIDTH
        tree.nodes.active = node
        redraw()
        return {"FINISHED"}


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


class RemiNodes_MT_Add(Menu):
    bl_idname = "RemiNodes_MT_Add"
    bl_label = "Remi Nodes"

    def draw(self, context):
        layout = self.layout
        for identifier, label, icon in NODE_ITEMS:
            button = layout.operator("remi_nodes.add", text=label, icon=icon)
            button.node_type = identifier


def node_add_menu(self, context):
    """Put Remi nodes into the node editor's own Shift+A menu."""
    space = getattr(context, "space_data", None)
    if space is None or getattr(space, "tree_type", "") != TREE_ID:
        return
    layout = self.layout
    layout.separator()
    layout.label(text="Remi")
    for identifier, label, icon in NODE_ITEMS:
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
        layout.operator("remi_nodes.sync_board", text="Sync Board From Gallery", icon="FILE_REFRESH")
        tree = graph()
        if tree is not None:
            cards = len([n for n in tree.nodes if n.bl_idname in {"RemiNodeImage", "RemiNodeSplats"}])
            layout.label(text=f"{len(tree.nodes)} nodes · {cards} cards", icon="NODETREE")


CLASSES = (
    RemiImageGraph,
    RemiNodePrompt,
    RemiNodeMerge,
    RemiNodeImage,
    RemiNodeGenerate,
    RemiNodeSplat,
    RemiNodeSplats,
    RemiNodeMesh,
    RemiNodes_OT_OpenGraph,
    RemiNodes_OT_Generate,
    RemiNodes_OT_Splat,
    RemiNodes_OT_Mesh,
    RemiNodes_OT_CardPin,
    RemiNodes_OT_CardOpen,
    RemiNodes_OT_UseLatest,
    RemiNodes_OT_UseSeed,
    RemiNodes_OT_Focus,
    RemiNodes_OT_SyncBoard,
    RemiNodes_OT_AddNode,
    RemiNodes_MT_Add,
    RemiNodes_PT_Panel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    try:
        bpy.types.NODE_MT_add.remove(node_add_menu)
    except (ValueError, RuntimeError):
        pass
    bpy.types.NODE_MT_add.append(node_add_menu)


def unregister():
    if bpy.app.timers.is_registered(poll_results):
        bpy.app.timers.unregister(poll_results)
    try:
        bpy.types.NODE_MT_add.remove(node_add_menu)
    except (ValueError, RuntimeError):
        pass
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
