"""Actual Blender + isolated worker + loopback Remi fixture; no paid execution."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import time

import bpy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tests"))
from test_cloud_generation import FixtureServer,native_gaussians,png_image,TOKEN,SIGNATURE

spec=importlib.util.spec_from_file_location("remi",ROOT/"__init__.py",submodule_search_locations=[str(ROOT)])
remi=importlib.util.module_from_spec(spec);sys.modules["remi"]=remi;spec.loader.exec_module(remi)
from remi.features.cloud import tick,before_load
from remi.features.cloud.jobs import Remi_OT_CloudGenerate
from remi.integrations.remi_cloud.client import runtime
from remi.blender.gaussian_splats import is_splat_object

remi.register()
server=FixtureServer()
root=tempfile.TemporaryDirectory(prefix="remi-blender-cloud-")
try:
    state=bpy.context.window_manager.remi_cloud
    state.base_url=server.api;state.output=root.name
    assert not Remi_OT_CloudGenerate.poll(bpy.context)
    assert bpy.ops.remi.cloud_login()=={"FINISHED"}
    def wait(predicate):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            tick()
            if predicate():return
            time.sleep(.02)
        raise AssertionError(f"Cloud fixture did not reach expected state: {runtime.status()}")
    wait(lambda:state.signed_in and not state.busy)
    assert not Remi_OT_CloudGenerate.poll(bpy.context)  # No invented default spending cap.
    state.max_cost_eur="0.001";state.prompt="A blue teapot";state.width=state.height=256
    tick();assert Remi_OT_CloudGenerate.poll(bpy.context)
    original=bpy.data.objects.get("Cube");assert original is not None
    original_mesh=original.data.as_pointer();original_count=len(bpy.data.objects)
    assert bpy.ops.remi.cloud_generate()=={"FINISHED"}
    wait(lambda:bool(state.output_path) and not state.busy)
    image_path=Path(state.output_path)
    assert image_path.read_bytes()==png_image()
    assert state.billing_message=="Billing pending; spending hold retained."
    assert bpy.ops.remi.cloud_import()=={"FINISHED"}
    loaded=[image for image in bpy.data.images if image.filepath==str(image_path)]
    assert len(loaded)==1 and tuple(loaded[0].size)==(256,256)
    assert len(loaded[0].pixels)>0
    assert len(bpy.data.objects)==original_count
    assert state.image==str(image_path)
    state.operation="generate";state.gaussians="32768";server.artifact=native_gaussians()
    tick();assert bpy.ops.remi.cloud_generate()=={"FINISHED"}
    wait(lambda:state.result_operation=="generate" and bool(state.output_path) and not state.busy)
    assert bpy.ops.remi.cloud_import()=={"FINISHED"}
    splat=bpy.context.object
    assert is_splat_object(splat) and splat["remi_splat_count"]==32768
    assert splat["remi_cloud_job_id"]==state.job_id
    assert bpy.data.objects.get("Cube")==original and original.data.as_pointer()==original_mesh
    assert Path(splat["remi_source_image"]).read_bytes()==png_image()
    # Unknown acceptance cannot accidentally start a fresh request from the same button.
    state.operation="generate_image";server.artifact=png_image();server.fault="lost_job"
    tick();assert bpy.ops.remi.cloud_generate()=={"FINISHED"}
    wait(lambda:runtime.status().get("state")=="error")
    assert state.request_path and not Remi_OT_CloudGenerate.poll(bpy.context)
    count=len(server.jobs)
    assert bpy.ops.remi.cloud_resume()=={"FINISHED"}
    wait(lambda:bool(state.output_path) and not state.busy)
    assert len(server.jobs)==count
    # Explicit local stop never calls the server cancellation endpoint.
    state.operation="generate_image";server.result_state="running"
    assert bpy.ops.remi.cloud_generate()=={"FINISHED"}
    wait(lambda:runtime.status().get("state")=="job_running")
    saved=Path(state.request_path)
    assert bpy.ops.remi.cloud_stop_waiting()=={"FINISHED"}
    assert saved.exists() and not runtime.following
    assert not any(r["path"].endswith("/cancel") for r in server.requests)
    # Scene save contains neither bearer nor signed URL and does not serialize login.
    blend=Path(root.name)/"cloud-result.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    assert TOKEN.encode() not in blend.read_bytes() and SIGNATURE.encode() not in blend.read_bytes()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    assert runtime.process is None and saved.exists()
    assert not bpy.context.window_manager.remi_cloud.signed_in
    assert any(is_splat_object(obj) and obj.get("remi_splat_count")==32768 for obj in bpy.data.objects)
    assert len(bpy.data.objects["Cube"].data.vertices)==8
    assert bpy.context.window_manager.remi_cloud.bl_rna.properties["signed_in"].is_skip_save
    # Register/unregister lifecycle and the actual source operator controls remain intact.
    remi.unregister();assert not hasattr(bpy.types.WindowManager,"remi_cloud")
    remi.register();assert hasattr(bpy.context.window_manager,"remi_cloud")
    assert not bpy.context.window_manager.remi_cloud.signed_in
    print("REMI_CLOUD_GENERATION_REGRESSION_OK: authenticated image/native calls, verified import, pending billing, recovery and scene isolation")
finally:
    runtime.cleanup();server.close();remi.unregister();root.cleanup()
