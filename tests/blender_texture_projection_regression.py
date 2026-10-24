"""Texture workspace, mesh exchange, material import, and worker validation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if "remi" not in sys.modules:
    spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    remi = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = remi
    spec.loader.exec_module(remi)
else:
    remi = sys.modules["remi"]

from remi.app.ui.workspace import active_workspace
from remi.blender.gaussian_splats import import_splats
from remi.blender.splat_projection import (
    apply_projection_material,
    export_mesh_archive,
    import_projection_result,
    projection_available,
    projection_preview,
    resolve_splat_source,
    source_label,
    write_world_splat_archive,
)
from remi.blender.texture_layers import (
    add_layer,
    delete_layer,
    rebuild_layer_material,
    sync_base_layer,
)
from remi.features.texture import _before_load
from remi.features.texture.operators import runtime as texture_runtime
from remi.features.texture.panel import draw_texture
from remi.features.texture.service import appearance_source, can_project
from remi.integrations.splat_project.client import ProjectionJob
from remi.integrations.triposplat.splat_data import save


class Layout:
    def __init__(self):
        self.operators = []
        self.labels = []
        self.props = []

    def column(self, **kwargs):
        return self

    row = column
    box = column

    def prop(self, owner, name, **kwargs):
        self.props.append((owner, name, kwargs))

    def label(self, **kwargs):
        self.labels.append(kwargs.get("text", ""))

    def separator(self):
        pass

    def operator(self, name, **kwargs):
        value = SimpleNamespace(**kwargs)
        self.operators.append((name, kwargs, value))
        return value


def make_mesh():
    mesh = bpy.data.meshes.new("ProjectionPlane")
    mesh.from_pydata(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [],
        [(0, 1, 2), (0, 2, 3)],
    )
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(mesh.loops, [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)]):
        uv_layer.data[loop.index].uv = uv
    obj = bpy.data.objects.new("ProjectionPlane", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def make_png(path, rgba):
    image = bpy.data.images.new(f"test_{path.stem}", 2, 2)
    image.pixels = list(rgba) * 4
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    return path


def operator_names(layout):
    return [name for name, _kwargs, _value in layout.operators]


remi.register()
try:
    wm = bpy.context.window_manager
    assert hasattr(bpy.context.scene, "remi_texture")
    assert hasattr(wm, "remi_texture")
    assert bpy.ops.remi.select_workspace.poll()
    assert bpy.ops.remi.select_workspace(workspace="TEXTURE") == {"FINISHED"}
    assert active_workspace(bpy.context) == "TEXTURE"

    with tempfile.TemporaryDirectory(prefix="remi-texture-test-") as directory:
        directory = Path(directory)
        splat_archive = directory / "splats.npz"
        save(splat_archive, {
            "positions": np.array([[0.5, 0.5, 0.0]], dtype=np.float32),
            "scales": np.array([[0.4, 0.4, 0.01]], dtype=np.float32),
            "rotations": np.array([[1, 0, 0, 0]], dtype=np.float32),
            "opacities": np.array([1.0], dtype=np.float32),
            "sh_dc": np.array([[0.0, 0.0, 0.0]], dtype=np.float32),
        })
        splat = import_splats(bpy.context, splat_archive, display="ELLIPSOID")

        mesh = make_mesh()
        assert resolve_splat_source(mesh) == (None, None)
        mesh["remi_splat_source"] = splat
        resolved, archive = resolve_splat_source(mesh)
        assert resolved is splat and archive is None
        assert source_label(resolved, archive) == splat.name

        # Projection exports the mesh in world space, so the splat archive must
        # be transformed into the same frame before the worker sees it.
        splat.location = (2.0, 3.0, 0.0)
        splat.rotation_euler = (0.0, 0.0, 3.141592653589793 / 2.0)
        splat.scale = (2.0, 2.0, 2.0)
        bpy.context.view_layer.update()
        world_archive = directory / "splats_world.npz"
        write_world_splat_archive(splat, world_archive)
        from remi.integrations.triposplat.splat_data import load
        world = load(world_archive)
        assert np.allclose(world["positions"][0], (1.0, 4.0, 0.0), atol=1e-5)
        splat.location = (0.0, 0.0, 0.0)
        splat.rotation_euler = (0.0, 0.0, 0.0)
        splat.scale = (1.0, 1.0, 1.0)

        bpy.context.scene.remi_generation.python = sys.executable
        bpy.context.scene.remi_texture.pack_images = False
        assert can_project(bpy.context)

        layout = Layout()
        draw_texture(layout, bpy.context)
        names = operator_names(layout)
        assert "remi.project_splats" in names
        project_operator = next(value for name, _kwargs, value in layout.operators if name == "remi.project_splats")
        assert project_operator.enabled
        assert "remi.texture_preview" not in names
        assert not bpy.ops.remi.texture_preview.poll()

        exchange = directory / "mesh.npz"
        export_mesh_archive(bpy.context, mesh, exchange)
        with np.load(exchange, allow_pickle=False) as data:
            assert data["positions"].shape == (2, 3, 3)
            assert data["normals"].shape == (2, 3, 3)
            assert data["uvs"].shape == (2, 3, 2)
            assert data["triangle_count"] == 2
            assert np.allclose(data["positions"][0, 0], (0, 0, 0))

        paths = {
            "albedo": str(make_png(directory / "albedo.png", (1.0, 0.0, 0.0, 1.0))),
            "normal": str(make_png(directory / "normal.png", (0.5, 0.5, 1.0, 1.0))),
            "confidence": str(make_png(directory / "confidence.png", (0.8, 0.8, 0.8, 1.0))),
            "report": str(directory / "report.json"),
        }
        (directory / "report.json").write_text("{}", encoding="utf-8")
        material = import_projection_result(
            mesh, paths, source_label_value=splat.name, pack_images=False
        )
        assert projection_available(mesh)
        assert projection_preview(mesh) == "TEXTURE"
        assert mesh.data.materials[0] == material
        assert mesh["remi_texture_albedo"] == paths["albedo"]
        assert bpy.ops.remi.texture_preview.poll()
        assert bpy.ops.remi.texture_preview(mode="CONFIDENCE") == {"FINISHED"}
        assert projection_preview(mesh) == "CONFIDENCE"
        assert mesh.data.materials[0].name.endswith("projected confidence")
        apply_projection_material(mesh, "TEXTURE")
        assert projection_preview(mesh) == "TEXTURE"

        # Keep Baked reuses a normal map that was already wired into the mesh
        # (for example by the existing Bake Normal action) instead of replacing
        # it with the splat-derived map.
        baked_image = bpy.data.images.new("BakedShapeNormal", 2, 2)
        baked = bpy.data.materials.new("BakedShape")
        baked.use_nodes = True
        baked_nodes = baked.node_tree.nodes
        baked_links = baked.node_tree.links
        baked_nodes.clear()
        baked_out = baked_nodes.new("ShaderNodeOutputMaterial")
        baked_bsdf = baked_nodes.new("ShaderNodeBsdfPrincipled")
        baked_tex = baked_nodes.new("ShaderNodeTexImage")
        baked_tex.image = baked_image
        baked_map = baked_nodes.new("ShaderNodeNormalMap")
        baked_links.new(baked_bsdf.outputs["BSDF"], baked_out.inputs["Surface"])
        baked_links.new(baked_tex.outputs["Color"], baked_map.inputs["Color"])
        baked_links.new(baked_map.outputs["Normal"], baked_bsdf.inputs["Normal"])
        mesh.data.materials.clear()
        mesh.data.materials.append(baked)
        import_projection_result(
            mesh,
            paths,
            source_label_value=splat.name,
            pack_images=False,
            normal_mode="EXISTING",
        )
        projected = mesh.data.materials[0]
        normal_node = next(node for node in projected.node_tree.nodes if node.name == "remi_projected_normal")
        assert normal_node.image == baked_image
        assert mesh["remi_texture_normal_mode"] == "EXISTING"

        # A 3D projection becomes the bottom/base layer, and view layers can be
        # added, hidden, and deleted without rebuilding pixels.
        base = sync_base_layer(mesh)
        assert base is not None and base.kind == "BASE"
        rebuild_layer_material(mesh)
        assert len(mesh.remi_texture_layers) == 1
        assert mesh.data.materials[0].name.endswith("layered")
        assert any(node.type == "NORMAL_MAP" for node in mesh.data.materials[0].node_tree.nodes)

        view_png = make_png(directory / "view_layer.png", (0.1, 0.8, 0.2, 0.5))
        view_layer = add_layer(
            mesh,
            "View",
            view_png,
            kind="VIEW",
            alpha_mode="IMAGE",
            pack=True,
        )
        assert len(mesh.remi_texture_layers) == 2
        layered = mesh.data.materials[0]
        assert any(node.type == "MIX_RGB" for node in layered.node_tree.nodes)
        assert any(node.name.startswith("remi_layer_") and node.type == "TEX_IMAGE"
                   for node in layered.node_tree.nodes)
        assert mesh.remi_texture_layers[0].kind == "BASE"

        # Direct property toggles call the rebuild callback.
        def layer_texture_count():
            return sum(
                1
                for node in mesh.data.materials[0].node_tree.nodes
                if node.name.startswith("remi_layer_") and node.type == "TEX_IMAGE"
            )

        assert layer_texture_count() == 2
        view_layer.visible = False
        assert layer_texture_count() == 1
        view_layer.visible = True
        assert layer_texture_count() == 2
        view_layer.opacity = 0.25
        assert mesh.data.materials[0].name.endswith("layered")

        assert bpy.ops.remi.delete_texture_layer.poll()
        view_index = next(
            index for index, layer in enumerate(mesh.remi_texture_layers) if layer.kind == "VIEW"
        )
        assert bpy.ops.remi.delete_texture_layer(index=view_index) == {"FINISHED"}
        assert len(mesh.remi_texture_layers) == 1
        assert delete_layer(mesh, 0)
        assert len(mesh.remi_texture_layers) == 0
        assert hasattr(bpy.ops.remi, "project_view")

        layout = Layout()
        draw_texture(layout, bpy.context)
        names = operator_names(layout)
        assert "remi.texture_preview" in names
        assert any(entry[0] == "remi.texture_preview" and getattr(entry[2], "mode", None) == "CONFIDENCE"
                   for entry in layout.operators)

        # Client validation must fail before spawning a process.
        with mock.patch("remi.integrations.splat_project.client.subprocess.Popen") as spawn:
            try:
                ProjectionJob.start(
                    python=sys.executable,
                    archive=directory / "missing.npz",
                    mesh=exchange,
                    output=directory,
                )
            except ValueError as exc:
                assert "Gaussian archive not found" in str(exc)
            else:
                raise AssertionError("missing archive accepted")
            spawn.assert_not_called()
        with mock.patch("remi.integrations.splat_project.client.subprocess.Popen") as spawn:
            try:
                ProjectionJob.start(
                    python=sys.executable,
                    archive=splat_archive,
                    mesh=directory / "missing.npz",
                    output=directory,
                )
            except ValueError as exc:
                assert "Mesh archive not found" in str(exc)
            else:
                raise AssertionError("missing mesh accepted")
            spawn.assert_not_called()
        with mock.patch("remi.integrations.splat_project.client.subprocess.Popen") as spawn:
            try:
                ProjectionJob.start(
                    python=directory / "missing-python.exe",
                    archive=splat_archive,
                    mesh=exchange,
                    output=directory,
                )
            except ValueError as exc:
                assert "Python" in str(exc)
            else:
                raise AssertionError("missing Python accepted")
            spawn.assert_not_called()

        identity = [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)]
        with mock.patch("remi.integrations.splat_project.client.subprocess.Popen") as spawn:
            try:
                ProjectionJob.start_view(
                    python=sys.executable,
                    mesh=exchange,
                    view_image=directory / "missing-view.png",
                    view_matrix=identity,
                    projection_matrix=identity,
                    output=directory,
                )
            except ValueError as exc:
                assert "View image not found" in str(exc)
            else:
                raise AssertionError("missing view image accepted")
            spawn.assert_not_called()
        with mock.patch("remi.integrations.splat_project.client.subprocess.Popen") as spawn:
            try:
                ProjectionJob.start_view(
                    python=directory / "missing-python.exe",
                    mesh=exchange,
                    view_image=view_png,
                    view_matrix=identity,
                    projection_matrix=identity,
                    output=directory,
                )
            except ValueError as exc:
                assert "Python" in str(exc)
            else:
                raise AssertionError("missing view Python accepted")
            spawn.assert_not_called()

        # Loading another file stops an in-flight projector and cleans temp files.
        temporary = directory / "temporary-mesh.npz"
        temporary.write_bytes(b"ephemeral")
        fake = mock.Mock()
        texture_runtime.job = fake
        texture_runtime.temp_paths = (temporary,)
        _before_load(None)
        fake.cancel.assert_called_once()
        assert texture_runtime.job is None
        assert not temporary.exists()

        # The panel disables projection when no linked source is available.
        mesh.pop("remi_splat_source", None)
        mesh.pop("remi_mesh_source_object", None)
        bpy.context.scene.remi_texture.source_object = None
        layout = Layout()
        draw_texture(layout, bpy.context)
        project_operator = next(value for name, _kwargs, value in layout.operators if name == "remi.project_splats")
        assert not project_operator.enabled
finally:
    bpy.context.window_manager.remi_session.active = False
    bpy.context.window_manager.remi_generation.busy = False
    bpy.context.window_manager.remi_generation.mesh_busy = False
    bpy.context.window_manager.remi_texture.busy = False
    remi.unregister()
assert not hasattr(bpy.types.Scene, "remi_texture")
assert not hasattr(bpy.types.WindowManager, "remi_texture")
print("REMI_TEXTURE_PROJECTION_REGRESSION_OK")
