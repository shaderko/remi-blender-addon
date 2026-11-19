"""Real GLB → evaluated copy → native comparison → organic runtime → accept.

Factory-startup Blender only. --input <glb> --output <folder> --mesh 0 2 9 11
Never saves the input or opens an existing project/profile.
"""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("REMI_TEST_DEPENDENCIES"):
    sys.path.insert(0, os.environ["REMI_TEST_DEPENDENCIES"])
PACKAGE = os.environ.get("REMI_TEST_PACKAGE", "remi")
if PACKAGE == "remi":
    exec(compile((ROOT / "tests/blender_checkout_bootstrap.py").read_text(),
                 str(ROOT / "tests/blender_checkout_bootstrap.py"), "exec"))
remi = importlib.import_module(PACKAGE)
runtime = importlib.import_module(PACKAGE+".features.retopology.instant_meshes.runtime").runtime


def wait():
    deadline = time.monotonic()+120
    while runtime.solve_stage or runtime.busy:
        assert time.monotonic() < deadline, runtime.last_error
        delay = runtime.poll()
        time.sleep(delay if delay is not None else .001)
    assert not runtime.last_error, runtime.last_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mesh", type=int, nargs="+", default=[0,2,9,11])
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    original_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    if PACKAGE == "remi":
        remi.register()
    try:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.import_scene.gltf(filepath=str(args.input))
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        # The GLB contains hidden source variants. Expose only this disposable
        # test scene's imported copies; never change the original file.
        for obj in meshes:
            if obj.name not in bpy.context.scene.collection.objects:
                bpy.context.scene.collection.objects.link(obj)
            obj.hide_viewport = False
        bpy.context.view_layer.update()
        settings = bpy.context.scene.remi_instant_meshes
        settings.target_faces = 1200
        settings.deterministic = True
        settings.preserve_creases = False
        settings.auto_guides = False
        settings.adaptive_method = "SURFACE"
        settings.adaptive_error = .01
        settings.adaptive_face_limit = 25000
        settings.adaptive_max_seconds = 60
        results = []
        for index in args.mesh:
            obj = meshes[index]
            obj.hide_set(False)
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            original = runtime._evaluated_triangles(obj)
            object_count = len(bpy.context.scene.objects)
            settings.adaptive_quads = False
            runtime.start(obj, settings)
            wait()
            native_quality = dict(runtime.preview_quality)
            settings.adaptive_quads = True
            runtime.update_preview()
            wait()
            report = dict(runtime.adaptive_report)
            assert len(bpy.context.scene.objects) == object_count
            for before, after in zip(original, runtime._evaluated_triangles(obj)):
                np.testing.assert_array_equal(before, after)
            if index in (9,11):
                assert not report["rebuilt"] and runtime.adaptive_failed, report
                assert not bpy.ops.remi.instant_meshes_accept.poll()
                assert "NOT APPLIED" in settings.status
                assert bpy.ops.remi.instant_meshes_use_native() == {"FINISHED"}
                assert runtime.can_accept and not settings.adaptive_quads
                runtime.shutdown()
            else:
                assert report["rebuilt"] and report["backend"] == "FIELD_ALIGNED", report
                assert report["runtime_source_preparation"]["stitched_vertices"] > 0
                assert runtime.preview_quality["quad_fraction"] == 1
                assert runtime.preview_quality["components"] == 1
                assert runtime.preview_quality["nonmanifold_edges"] == runtime.preview_quality["boundary_edges"] == 0
                assert report["ideal_valence_fraction"] >= .90, report["ideal_valence_fraction"]
                assert report["poles"] <= max(4, report["output_faces"]*.05), report["poles"]
                assert report["output_quality"]["aspect_p95"] <= 2.5
                assert report["output_quality"]["angle_error_p95_deg"] <= 35
                assert report["sampled_patch_error_max"] <= report["shape_error_limit"]
                assert runtime.preview_normals.shape == runtime.preview[0].shape
                assert np.isfinite(runtime.preview_normals).all()
                count = len(runtime.preview[1])
                assert bpy.ops.remi.instant_meshes_accept() == {"FINISHED"}
                accepted = bpy.context.active_object
                assert accepted != obj and len(accepted.data.polygons) == count
                assert all(len(face.vertices) == 4 for face in accepted.data.polygons)
                for before, after in zip(original, runtime._evaluated_triangles(obj)):
                    np.testing.assert_array_equal(before, after)
                bpy.data.objects.remove(accepted, do_unlink=True)
                obj.hide_set(False)
            results.append(dict(mesh_index=index, native_quality=native_quality, adaptive=report))
            (args.output / "report.json").write_text(json.dumps(results, indent=2, allow_nan=False), encoding="utf-8")
            print("REMI_REAL_SURFACE_RUNTIME", index, "rebuilt", report["rebuilt"], report["reason"], flush=True)
        assert hashlib.sha256(args.input.read_bytes()).hexdigest() == original_hash
    finally:
        runtime.shutdown()
        if PACKAGE == "remi":
            remi.unregister()
    print("REMI_REAL_SURFACE_RUNTIME_OK", args.output, flush=True)


if __name__ == "__main__":
    main()
