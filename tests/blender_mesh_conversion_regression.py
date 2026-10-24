"""Mesh conversion UI, per-object import, cleanup and reconstructed-flow regression."""
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

from remi.app import flow
from remi.blender.gaussian_splats import (
    import_splats, native_splats_available, splat_arrays, write_splat_archive,
)
from remi.blender.reconstructed_mesh import (
    import_reconstructed_mesh, is_reconstructed_mesh, mesh_for_splat,
)
from remi.features.generation import _before_load
from remi.features.generation.mesh_operators import (
    runtime as mesh_runtime, splat_source,
)
from remi.features.generation.panel import draw_generation
from remi.integrations.splat_mesh.client import MeshJob
from remi.integrations.triposplat.splat_data import load, save
from remi.workflow.session import runtime as session_runtime


class Layout:
    def __init__(self):
        self.operators = []
        self.labels = []

    def column(self, **kwargs):
        return self

    row = column
    box = column

    def prop(self, *args, **kwargs):
        pass

    def label(self, **kwargs):
        self.labels.append(kwargs.get("text", ""))

    def separator(self):
        pass

    def popover(self, **kwargs):
        pass

    def operator(self, name, **kwargs):
        self.operators.append(name)
        return SimpleNamespace(**kwargs)


def draw():
    layout = Layout()
    draw_generation(layout, bpy.context)
    return layout


def make_ply(path, color=True):
    props = "property float x\nproperty float y\nproperty float z\n"
    body = []
    rgba = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
    if color:
        props += "property uchar red\nproperty uchar green\nproperty uchar blue\n"
    coords = ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0))
    for (x, y, z), rgb in zip(coords, rgba):
        body.append(f"{x} {y} {z}" + (f" {rgb[0]} {rgb[1]} {rgb[2]}" if color else ""))
    path.write_text(
        "ply\nformat ascii 1.0\nelement vertex 4\n" + props
        + "element face 2\nproperty list uchar int vertex_indices\nend_header\n"
        + "\n".join(body) + "\n3 0 1 2\n3 0 2 3\n",
        encoding="ascii",
    )


def arrays(offset=0.0):
    return {
        "positions": np.array([
            [offset, 0, 0], [offset + 0.5, 0, 0], [offset, 0.5, 0],
        ], dtype=np.float32),
        "scales": np.full((3, 3), 0.1, dtype=np.float32),
        "rotations": np.tile(np.array([1, 0, 0, 0], dtype=np.float32), (3, 1)),
        "opacities": np.ones(3, dtype=np.float32),
        "sh_dc": np.zeros((3, 3), dtype=np.float32),
    }


remi.register()
try:
    assert not bpy.ops.remi.create_mesh.poll()
    assert not bpy.ops.remi.cancel_mesh.poll()
    with tempfile.TemporaryDirectory(prefix="remi-mesh-test-") as directory:
        directory = Path(directory)
        archive = directory / "splats.npz"
        save(archive, arrays())
        display = "NATIVE" if native_splats_available() else "ELLIPSOID"
        splat = import_splats(bpy.context, archive, display=display)
        assert splat_source(bpy.context) is splat
        assert bpy.ops.remi.create_mesh.poll()
        layout = draw()
        assert "remi.create_mesh" in layout.operators

        # The object itself carries the complete Gaussian data. It can be
        # meshed after the generation archive is moved/deleted.
        extracted = splat_arrays(splat)
        for name, value in arrays().items():
            assert np.array_equal(extracted[name], value), name
        ephemeral = directory / "ephemeral.npz"
        write_splat_archive(splat, ephemeral)
        for name, value in arrays().items():
            assert np.array_equal(load(ephemeral)[name], value), name
        archive.unlink()
        assert splat_source(bpy.context) is splat
        assert bpy.ops.remi.create_mesh.poll()

        state = bpy.context.window_manager.remi_generation
        state.mesh_busy = True
        state.mesh_status = "Building surface…"
        assert not bpy.ops.remi.create_mesh.poll()
        assert not bpy.ops.remi.generate_splats.poll()
        assert not bpy.ops.remi.start_session.poll()
        assert not bpy.ops.remi.select_workspace.poll()
        layout = draw()
        assert "remi.cancel_mesh" in layout.operators
        state.mesh_busy = False
        state.mesh_status = ""
        state.mesh_status_kind = "IDLE"

        mesh_path = directory / "mesh.ply"
        make_ply(mesh_path)
        before = set(bpy.data.objects)
        obj = import_reconstructed_mesh(bpy.context, mesh_path, source_object=splat)
        assert set(bpy.data.objects) == before | {obj}
        assert is_reconstructed_mesh(obj)
        assert bpy.context.active_object is obj
        assert obj.data.color_attributes.get("Col") is not None
        assert len(obj.data.materials) == 1 and obj.data.materials[0].use_nodes
        assert splat.hide_get()
        assert obj["remi_mesh_source_object"] == splat.name
        assert splat["remi_mesh_object"] == obj.name
        assert mesh_for_splat(splat) is obj

        # A second model in the same file must remain independently meshable;
        # the first result must not become a global "only one model" cache.
        archive2 = directory / "splats_2.npz"
        save(archive2, arrays(offset=3.0))
        splat2 = import_splats(bpy.context, archive2, display=display)
        assert splat_source(bpy.context) is splat2
        assert mesh_for_splat(splat2) is None
        state.mesh_status = "Mesh ready · 2 triangles"
        state.mesh_status_kind = "SUCCESS"
        state.mesh_source_object = splat.name
        layout = draw()
        assert "remi.create_mesh" in layout.operators
        assert "Recreate Mesh" not in layout.labels
        assert bpy.ops.remi.create_mesh.poll()
        bpy.context.view_layer.objects.active = splat
        assert mesh_for_splat(splat) is obj
        layout = draw()
        assert "remi.create_mesh" in layout.operators
        bpy.context.view_layer.objects.active = splat2

        # A failed import must not leave a mesh or material behind.
        counts = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials))
        with mock.patch("remi.blender.reconstructed_mesh._vertex_color_material", side_effect=RuntimeError("material failed")):
            try:
                import_reconstructed_mesh(bpy.context, mesh_path, source_object=splat2)
            except RuntimeError:
                pass
            else:
                raise AssertionError("import failure expected")
        assert counts == (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials))
        assert bpy.context.active_object is splat2

        # The reconstructed preset must avoid the destructive default remesh.
        flow_settings = bpy.context.scene.remi_flow
        flow_settings.preset = "RECONSTRUCTED"
        document = flow.selected_document(bpy.context)
        assert document["actions"] == ["DECIMATE"]
        assert document["settings"]["decimation_target_faces"] == 120000
        assert flow.validate_plan(bpy.context, document) is None

        # The UI exposes the new controls in the gear popover.
        from remi.features.generation.panel import Remi_PT_GenerationSettings
        settings_layout = Layout()
        Remi_PT_GenerationSettings.draw(SimpleNamespace(layout=settings_layout), bpy.context)
        settings = bpy.context.scene.remi_generation
        assert settings.mesh_detail in {"160", "224", "288"}
        assert settings.mesh_smoothing in {"6", "12", "20"}
        assert settings.mesh_output

        # Client validation must fail before spawning a process.
        with mock.patch("remi.integrations.splat_mesh.client.subprocess.Popen") as spawn:
            try:
                MeshJob.start(python=sys.executable, archive=directory / "missing.npz", output=directory)
            except ValueError as exc:
                assert "not found" in str(exc)
            else:
                raise AssertionError("missing archive accepted")
            spawn.assert_not_called()
        with mock.patch("remi.integrations.splat_mesh.client.subprocess.Popen") as spawn:
            try:
                MeshJob.start(python=directory / "missing-python.exe", archive=ephemeral, output=directory)
            except ValueError as exc:
                assert "Python" in str(exc)
            else:
                raise AssertionError("missing Python accepted")
            spawn.assert_not_called()

        temporary = directory / "mesh-source.npz"
        temporary.write_bytes(b"ephemeral")
        fake = mock.Mock()
        mesh_runtime.job = fake
        mesh_runtime.temp_archive = temporary
        _before_load(None)
        fake.cancel.assert_called_once()
        assert mesh_runtime.job is None
        assert not temporary.exists()

        # Mesh sessions remain ordinary mesh sessions once imported.
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        splat.select_set(False)
        splat2.select_set(False)
        assert bpy.ops.remi.start_session.poll()
        session_runtime.begin(bpy.context, obj)
        session_runtime.cancel(bpy.context)
finally:
    bpy.context.window_manager.remi_session.active = False
    bpy.context.window_manager.remi_generation.mesh_busy = False
    bpy.context.window_manager.remi_generation.busy = False
    remi.unregister()
assert not hasattr(bpy.types.Scene, "remi_generation")
print("REMI_MESH_CONVERSION_REGRESSION_OK")
