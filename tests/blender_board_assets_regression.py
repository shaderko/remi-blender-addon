"""Asset identity/lineage, native camera framing, and real isolated flow jobs."""

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
from remi.features.board.assets import Asset, Catalog
from remi.features.board.jobs import LocalBlenderJob, Runner
from remi.features.board.splat_preview import camera_frame
from remi.features.generation.settings import default_python


def wait(job):
    deadline = time.monotonic() + 120
    while job.poll() is None:
        if time.monotonic() > deadline:
            job.cancel()
            raise AssertionError("Local board worker timed out")
        time.sleep(0.05)
    try:
        return job.result()
    except Exception:
        print((job.directory / "worker.log").read_text(encoding="utf-8", errors="replace"))
        raise


remi.register()
try:
    assert "asset_id" in bpy.ops.remi_board.create_mesh.get_rna_type().properties
    assert "asset_id" in bpy.ops.remi_board.run_flow.get_rna_type().properties
    with tempfile.TemporaryDirectory(prefix="remi-board-assets-") as temporary:
        temporary = Path(temporary)
        image_path = temporary / "source.png"
        image_path.write_bytes(b"source image identity fixture")
        catalog = Catalog()
        image = catalog.image(image_path, "Original image")
        assert catalog.image(image_path) is image
        first = catalog.derive(image.id, "SPLAT", "GENERATE_3D", {"seed": 1})
        second = catalog.derive(image.id, "SPLAT", "GENERATE_3D", {"seed": 2})
        assert first.id != second.id != image.id
        assert image.kind == "IMAGE" and first.parent == second.parent == image.id
        assert [asset.id for asset in catalog.ancestors(first.id)] == [first.id, image.id]
        roundtrip = Catalog(json.loads(json.dumps(catalog.documents())))
        assert roundtrip.assets[first.id].state == "INTERRUPTED"
        assert roundtrip.assets[first.id].settings == {"seed": 1}
        first.state = second.state = "READY"
        malformed = image.document()
        malformed["yaw"] = float("nan")
        assert not Catalog([malformed]).assets
        revision = Asset("edited", "MESH", artifact="current.glb", preview="current.png",
                         revisions=[{"artifact": "original.glb", "preview": "original.png"}])
        restored_revision = Catalog([revision.document()]).assets[revision.id]
        assert restored_revision.files() == {"current.glb", "current.png", "original.glb", "original.png"}
        malformed = revision.document()
        malformed["revisions"] = [{"artifact": 123}]
        assert not Catalog([malformed]).assets

        scales = np.array([[0.2,0.02,0.04]], dtype=np.float32)
        identity = np.array([[1,0,0,0]], dtype=np.float32)
        arrays = {"positions": np.zeros((1,3)), "scales": scales, "rotations": identity,
                  "opacities": np.array([1.0]), "sh_dc": np.zeros((1,3))}
        centre, location = camera_frame(arrays)
        assert np.isfinite(location).all() and location[1] < centre[1]
        arrays["opacities"][:] = 0
        assert np.isfinite(camera_frame(arrays)[1]).all()

        ply = temporary / "cube.ply"
        vertices = [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                    (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]
        faces = [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
        ply.write_text("ply\nformat ascii 1.0\nelement vertex 8\nproperty float x\nproperty float y\nproperty float z\n"
                       "property uchar red\nproperty uchar green\nproperty uchar blue\nelement face 6\nproperty list uchar int vertex_indices\nend_header\n" +
                       "".join(f"{x} {y} {z} 80 160 210\n" for x,y,z in vertices) +
                       "".join("4 " + " ".join(map(str, face)) + "\n" for face in faces), encoding="ascii")
        digest = hashlib.sha256(ply.read_bytes()).hexdigest()
        mesh = catalog.derive(first.id, "MESH", "CREATE_MESH")
        mesh.state, mesh.artifact = "READY", str(ply)
        document = {"version": 1, "name": "Board Full Flow", "actions": ["REMESH", "DECIMATE", "UV", "BAKE_ALL"],
                    "settings": {"bake_texture_size": 256, "decimation_passes": 1, "target_percentage": 0.5,
                                 "decimation_with_texture": False, "voxel_size": 0.08, "bake_half_scale": False}}
        result = catalog.derive(mesh.id, "MESH", "FLOW", {"flow": document, "output": str(temporary)})
        before = [(obj.name, obj.matrix_world.copy(), obj.select_get()) for obj in bpy.context.scene.objects]
        active = bpy.context.active_object
        runner = Runner()
        runner.start(bpy.context, catalog, result)
        assert result.state == "RUNNING"
        wait(runner.job)
        runner.tick(bpy.context, catalog)
        assert result.state == "READY", result.status
        assert Path(result.artifact).suffix == ".glb" and Path(result.preview).is_file()
        assert hashlib.sha256(ply.read_bytes()).hexdigest() == digest
        assert bpy.context.active_object == active
        assert len(bpy.context.scene.objects) == len(before)
        for name, matrix, selected in before:
            assert bpy.data.objects[name].matrix_world == matrix
            assert bpy.data.objects[name].select_get() == selected
        glb = Path(result.artifact).read_bytes()
        assert glb[:4] == b"glTF"
        length, kind = struct.unpack_from("<II", glb, 12)
        assert kind == 0x4E4F534A
        gltf = json.loads(glb[20:20+length])
        assert "TEXCOORD_0" in gltf["meshes"][0]["primitives"][0]["attributes"]
        assert len(gltf.get("images", [])) >= 2, "Baked appearance was not included in the GLB"
        restored = Catalog(catalog.documents())
        assert restored.assets[result.id].parent == mesh.id
        assert restored.assets[result.id].state == "READY"
        assert restored.assets[result.id].settings["flow"] == document

        # Mesh + linked Gaussian appearance -> one textured GLB, without needing
        # an unintuitive mixed card selection.
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_plane_add(size=2)
        plane = bpy.context.object
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.uv.smart_project()
        bpy.ops.object.mode_set(mode="OBJECT")
        plane_glb = temporary / "texture_plane.glb"
        bpy.ops.export_scene.gltf(filepath=str(plane_glb), export_format="GLB", use_selection=True)
        from remi.integrations.triposplat.splat_data import save as save_splats
        values = np.linspace(-0.9, 0.9, 12, dtype=np.float32)
        gx, gy = np.meshgrid(values, values)
        positions = np.stack((gx.ravel(), gy.ravel(), np.zeros(gx.size, dtype=np.float32)), axis=1)
        gaussian_archive = temporary / "texture_splats.npz"
        save_splats(gaussian_archive, {
            "positions": positions,
            "scales": np.tile(np.array([0.1, 0.1, 0.002], np.float32), (len(positions), 1)),
            "rotations": np.tile(np.array([1, 0, 0, 0], np.float32), (len(positions), 1)),
            "opacities": np.ones(len(positions), np.float32),
            "sh_dc": np.tile(np.array([(0.9-0.5)/0.28209479177387814,
                                        (0.2-0.5)/0.28209479177387814,
                                        (0.1-0.5)/0.28209479177387814], np.float32), (len(positions), 1))})
        splat = catalog.derive(image.id, "SPLAT", "GENERATE_3D")
        splat.state, splat.artifact = "READY", str(gaussian_archive)
        textured_parent = catalog.derive(splat.id, "MESH", "CREATE_MESH")
        textured_parent.state, textured_parent.artifact = "READY", str(plane_glb)
        textured_parent.links["texture_source"] = splat.id
        textured = catalog.derive(textured_parent.id, "MESH", "TEXTURE", {
            "python": default_python(),
            "output": str(temporary), "gaussian_source": str(gaussian_archive),
            "texture": {"texture_size": 512, "match_distance": 0.05,
                        "layer_width": 0.02, "normal_smoothing": 2}}, "Textured Mesh")
        runner.start(bpy.context, catalog, textured)
        wait(runner.job)
        runner.tick(bpy.context, catalog)
        assert textured.state == "READY", textured.status
        assert Path(textured.artifact).suffix == ".glb" and Path(textured.preview).is_file()
        glb = Path(textured.artifact).read_bytes()
        length, kind = struct.unpack_from("<II", glb, 12)
        gltf = json.loads(glb[20:20+length])
        assert "TEXCOORD_0" in gltf["meshes"][0]["primitives"][0]["attributes"]
        assert len(gltf.get("images", [])) >= 2, "Textured GLB did not embed albedo and normal"

        # Applying a manual edit to the same parent card while a bake is queued
        # must not silently substitute a newer mesh for the captured recipe.
        snapshot_options = dict(textured.settings, mesh_artifact=str(plane_glb))
        snapshot = catalog.derive(textured_parent.id, "MESH", "TEXTURE", snapshot_options)
        textured_parent.artifact = str(temporary / "newer-mesh-not-the-input.glb")
        with mock.patch.object(LocalBlenderJob, "start", return_value=SimpleNamespace(directory=temporary)) as launch:
            runner.start(bpy.context, catalog, snapshot)
            assert launch.call_args.kwargs["request"]["input"] == str(plane_glb)
        runner.job, runner.asset_id = None, ""
        catalog.assets.pop(snapshot.id)
        textured_parent.artifact = str(plane_glb)

        cancelled = catalog.derive(mesh.id, "MESH", "FLOW", {"flow": document})
        runner.cancel(bpy.context, catalog, cancelled.id)
        assert cancelled.state == "CANCELLED"
        assert ply.is_file() and image_path.is_file()
        invalid = catalog.derive(mesh.id, "MESH", "FLOW", {"output": ""})
        runner.tick(bpy.context, catalog)
        assert invalid.state == "ERROR" and runner.job is None
        assert "output folder" in invalid.status
        print("REMI_BOARD_ASSET_JOBS_OK", flush=True)
finally:
    remi.unregister()
print("REMI_BOARD_ASSETS_REGRESSION_OK", flush=True)
