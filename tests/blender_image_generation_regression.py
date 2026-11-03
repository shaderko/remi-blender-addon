"""Combined Create workspace: gallery cards, 3D actions, uploads, progress."""

import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py",
                                                 submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
else:
    remi = sys.modules["remi"]

from remi.app.ui.workspace import active_workspace
from remi.blender.gaussian_splats import splat_for_image
from remi.features.images.gallery import gallery as gallery_object
from remi.features.images.panel import draw_create
from remi.features.images.settings import dimensions
from remi.integrations.iris3b import missing_components
from remi.integrations.iris3b.constants import MODEL_FILES, TEXT_ENCODER_FILES

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFElEQVR4nGO8E6DBgA0wYRUdtBIADsMBZEZMM/8AAAAASUVORK5CYII="
)


class Layout:
    def __init__(self, entries=None, parent=None):
        self.entries = entries if entries is not None else []
        self.parent = parent
        self.enabled = True
        self.scale_y = 1
        self.alert = False

    def column(self, **kwargs):
        return Layout(self.entries, self)

    row = column
    box = column

    def prop(self, owner, property_name, **kwargs):
        self.entries.append(("prop", property_name, kwargs, self))

    def label(self, **kwargs):
        self.entries.append(("label", kwargs.get("text", ""), kwargs, self))

    def separator(self, **kwargs):
        pass

    def popover(self, **kwargs):
        self.entries.append(("popover", kwargs["panel"], kwargs, self))

    def operator(self, name, **kwargs):
        item = SimpleNamespace(parent=self, **kwargs)
        self.entries.append(("operator", name, kwargs, item))
        return item

    def template_icon(self, icon_value=0, scale=1.0):
        self.entries.append(("icon", icon_value, scale, self))

    def progress(self, factor=0.0, type="BAR", text=""):
        self.entries.append(("progress", factor, text, self))


def draw():
    layout = Layout()
    draw_create(layout, bpy.context)
    return layout.entries


def write_job(root, name, prompt, model="turbo", seed=7, count=2, source="generated"):
    job = root / name
    job.mkdir()
    images = []
    for index in range(count):
        path = job / f"image_{index:03d}.png"
        path.write_bytes(PNG)
        images.append({"file": path.name, "seed": seed + index})
    (job / "meta.json").write_text(json.dumps({
        "schema": 1, "model": model,
        "model_label": "Uploaded Image" if source == "upload" else
        ("Iris-3B Turbo" if model == "turbo" else "Iris-3B"),
        "prompt": prompt, "negative_prompt": "", "width": 1024, "height": 1024,
        "steps": 4 if model == "turbo" else 100, "cfg_scale": 1.0 if model == "turbo" else 3.0,
        "seed": seed, "count": count, "images": images, "source": source,
    }), encoding="utf-8")
    (job / "complete.json").write_text('{"schema": 1, "complete": true}', encoding="utf-8")
    return job


def make_model_files(models):
    for relative in MODEL_FILES["turbo"]:
        path = models / "iris-3b" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    for relative in TEXT_ENCODER_FILES:
        path = models / "Qwen3-VL-4B-Instruct" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


remi.register()
try:
    wm = bpy.context.window_manager
    scene = bpy.context.scene
    assert hasattr(scene, "remi_image") and hasattr(wm, "remi_image")
    assert active_workspace(bpy.context) == "GENERATE"
    assert "IMAGES" not in [item.identifier for item in
                            bpy.types.WindowManager.bl_rna.properties["remi_ui"].fixed_type.properties["workspace"].enum_items]

    settings = scene.remi_image
    assert settings.model == "turbo"
    assert dimensions(settings) == (1024, 1024)
    settings.size = "PORTRAIT"
    assert dimensions(settings) == (896, 1200)
    settings.size = "SQUARE"

    with tempfile.TemporaryDirectory(prefix="remi-create-test-") as temporary:
        temporary = Path(temporary)
        models = temporary / "models"
        settings.models = str(models)
        settings.output = str(temporary / "out")
        make_model_files(models)
        assert missing_components(models, "turbo") == []

        output = Path(settings.output)
        output.mkdir()
        write_job(output, "20260101_000000_a", "A red fox in fresh snow, golden hour", "turbo", 11, 2)
        write_job(output, "20260102_000000_b", "A glass heart filled with flowers", "base", 21, 1)
        write_job(output, "20260103_000000_c", "Uploaded horse reference", "upload", 0, 1, "upload")
        gallery_object.invalidate()
        wm.remi_image.refresh_token = 0
        gallery_object.icon = lambda path: 1

        entries = draw()
        props = {entry[1] for entry in entries if entry[0] == "prop"}
        assert "prompt" in props
        icons = [entry for entry in entries if entry[0] == "icon"]
        assert len(icons) == 4, len(icons)
        operators = {entry[1] for entry in entries if entry[0] == "operator"}
        assert {"remi.generate_image", "remi.add_image", "remi.refresh_images",
                "remi.select_image", "remi.generate_splats"} <= operators
        generate = next(entry for entry in entries if entry[0] == "operator" and entry[1] == "remi.generate_image")
        assert not generate[3].parent.enabled
        names = [entry[1] for entry in entries if entry[0] == "operator" and entry[1] == "remi.generate_splats"]
        assert len(names) == 4
        play = next(entry for entry in entries if entry[0] == "operator" and entry[1] == "remi.generate_splats")
        assert play[3].image_path.endswith(".png")

        settings.prompt = "A red fox"
        entries = draw()
        generate = next(entry for entry in entries if entry[0] == "operator" and entry[1] == "remi.generate_image")
        assert generate[3].parent.enabled

        records = gallery_object.refresh(settings.output, wm.remi_image.refresh_token, force=True)
        assert len(records) == 4
        selected = next(record for record in records if record.model == "turbo")
        wm.remi_image.selected = str(selected.path)
        entries = draw()
        operators = {entry[1] for entry in entries if entry[0] == "operator"}
        assert {"remi.regenerate_image", "remi.copy_image_prompt",
                "remi.delete_image", "remi.generate_splats"} <= operators

        # A splat carrying the source-image tag turns the card into a 3D result.
        mesh = bpy.data.meshes.new("CreateSplatProbe")
        splat = bpy.data.objects.new("CreateSplatProbe", mesh)
        scene.collection.objects.link(splat)
        splat["remi_gaussian_splats"] = 1
        splat["remi_source_image"] = str(selected.path.resolve())
        assert splat_for_image(selected.path) is splat
        entries = draw()
        operators = {entry[1] for entry in entries if entry[0] == "operator"}
        assert "remi.activate_splat" in operators
        assert bpy.ops.remi.activate_splat.poll()
        assert bpy.ops.remi.activate_splat(image_path=str(selected.path)) == {"FINISHED"}
        assert bpy.context.view_layer.objects.active is splat
        bpy.data.objects.remove(splat, do_unlink=True)
        bpy.data.meshes.remove(mesh)

        # Upload adds a persistent card and selects it.
        upload = temporary / "upload.png"
        upload.write_bytes(PNG)
        before = len(gallery_object.refresh(settings.output, wm.remi_image.refresh_token, force=True))
        assert bpy.ops.remi.add_image(filepath=str(upload)) == {"FINISHED"}
        after = gallery_object.refresh(settings.output, wm.remi_image.refresh_token, force=True)
        assert len(after) == before + 1
        uploaded = next(record for record in after if record.model == "upload" and "upload" in record.prompt.lower())
        assert uploaded.path.is_file()
        entries = draw()
        assert "remi.generate_splats" in {entry[1] for entry in entries if entry[0] == "operator"}

        # Image generation progress is shown as a card above the grid.
        wm.remi_image.busy = True
        wm.remi_image.progress = 0.5
        wm.remi_image.status = "Generating image 1/2"
        entries = draw()
        progress = [entry for entry in entries if entry[0] == "progress"]
        assert progress and progress[0][1] == 0.5
        assert active_workspace(bpy.context) == "GENERATE"
        assert not bpy.ops.remi.select_workspace.poll()
        assert not bpy.ops.remi.generate_image.poll()
        wm.remi_image.busy = False
        wm.remi_image.progress = 0.0

        # 3D generation progress is attached to the selected image card.
        wm.remi_image.selected = str(selected.path)
        wm.remi_generation.busy = True
        wm.remi_generation.source_image = str(selected.path)
        wm.remi_generation.progress = 0.42
        wm.remi_generation.status = "Generating…"
        entries = draw()
        progress = [entry for entry in entries if entry[0] == "progress"]
        assert progress and any(abs(entry[1] - 0.42) < 1e-5 for entry in progress)
        wm.remi_generation.busy = False
        wm.remi_generation.progress = 0.0

        assert bpy.ops.remi.regenerate_image(path=str(selected.path)) == {"FINISHED"}
        assert settings.prompt == selected.prompt
        assert settings.seed == selected.seed
        assert settings.model == "turbo"

        removed = gallery_object.remove(selected)
        assert removed and not selected.path.exists()
        remaining = gallery_object.refresh(settings.output, wm.remi_image.refresh_token, force=True)
        assert len(remaining) == before

        settings.prompt = "A red fox"
        assert bpy.ops.remi.generate_image.poll()
        wm.remi_image.busy = True
        assert active_workspace(bpy.context) == "GENERATE"
        assert not bpy.ops.remi.select_workspace.poll()
        assert not bpy.ops.remi.generate_image.poll()
        try:
            from remi.workflow.session import runtime as session_runtime
            original = bpy.context.active_object or bpy.data.objects.new("Cube", bpy.data.meshes.new("Cube"))
            if original.name not in bpy.context.scene.objects:
                bpy.context.scene.collection.objects.link(original)
            try:
                session_runtime.begin(bpy.context, original)
            except RuntimeError as exc:
                assert "image generation" in str(exc)
            else:
                raise AssertionError("A mesh session must not start during image generation")
        finally:
            wm.remi_image.busy = False
finally:
    bpy.context.window_manager.remi_image.busy = False
    bpy.context.window_manager.remi_generation.busy = False
    gallery_object.close()
    remi.unregister()

assert not hasattr(bpy.types.Scene, "remi_image")
print("REMI_IMAGE_GENERATION_REGRESSION_OK")


