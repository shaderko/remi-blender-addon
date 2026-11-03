"""Exercise a clean installed extension; launched by scripts/release.py."""
import bpy
import bmesh
import importlib
import os
import site
import time

module_name = "bl_ext.user_default.remi"
result = bpy.ops.preferences.addon_enable(module=module_name)
assert result == {"FINISHED"}, result
addon = importlib.import_module(module_name)
version_core = os.environ["REMI_EXPECTED_VERSION"].split("-", 1)[0].split("+", 1)[0]
expected_version = tuple(int(part) for part in version_core.split("."))
assert addon.bl_info["version"] == expected_version, addon.bl_info["version"]
assert addon.__package__ == module_name, addon.__package__
assert not site.ENABLE_USER_SITE
pymeshlab = importlib.import_module("pymeshlab")
assert os.path.realpath(pymeshlab.__file__).startswith(
    os.path.realpath(os.environ["BLENDER_USER_EXTENSIONS"]) + os.sep
), pymeshlab.__file__
assert "generate_alpha_wrap" in set(pymeshlab.filter_list())
print("REMI_INSTALLED_PYMESHLAB_WHEEL_OK")
image_panel = importlib.import_module(module_name + ".features.images.panel")
iris_worker = importlib.import_module(module_name + ".integrations.iris3b.worker")
assert hasattr(bpy.context.scene, "remi_image")
assert bpy.context.scene.remi_image.model in {"base", "turbo"}
assert hasattr(bpy.ops.remi, "generate_image")
assert hasattr(bpy.ops.remi, "download_iris")
assert (iris_worker.VENDOR / "iris3b" / "config.py").is_file()
assert image_panel.gallery is not None
print("REMI_INSTALLED_IRIS_IMAGES_OK")
instant_native = importlib.import_module(module_name + ".features.retopology.instant_meshes._native")
assert instant_native.Session is not None
assert instant_native.GUIDE_API_VERSION == 1
uv_engine = importlib.import_module(module_name + ".features.uv.engine")
uv_native = importlib.import_module(module_name + ".features.uv.engine._native")
assert os.path.realpath(uv_native.__file__).startswith(os.path.realpath(os.environ["BLENDER_USER_EXTENSIONS"]) + os.sep)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
uv_result = uv_engine.ensure_remi_uv(
    bpy.context.object, texture_size=256, margin_px=4,
    profile_id="HARD_SURFACE", replace_existing=True,
)
assert uv_result.success and uv_result.stats.valid, uv_result.error
assert 3.999 <= uv_result.stats.minimum_gap_px < 4.1, uv_result.stats.minimum_gap_px
print("REMI_INSTALLED_UV_GAP_SMOKE_OK")
# Exercise the new binding and packaged detector without user site-packages.
instant_runtime = importlib.import_module(module_name + ".features.retopology.instant_meshes.runtime").runtime
instant_settings = bpy.context.scene.remi_instant_meshes
assert not instant_settings.auto_guides and instant_settings.auto_quality_guard
bpy.ops.mesh.primitive_cylinder_add(vertices=32)
guide_source = bpy.context.object
object_count = len(bpy.context.scene.objects)
instant_settings.target_faces = 1000
instant_settings.auto_guides = True
instant_settings.deterministic = True
instant_runtime.start(guide_source, instant_settings)
deadline = time.monotonic() + 60
while instant_runtime.solve_stage or instant_runtime.session.active:
    assert time.monotonic() < deadline, instant_runtime.last_error
    instant_runtime.poll()
    time.sleep(0.01)
assert not instant_runtime.last_error, instant_runtime.last_error
assert instant_runtime.preview is not None and len(instant_runtime.automatic_guides) >= 3
assert instant_runtime.guidance_decision
assert len(bpy.context.scene.objects) == object_count
instant_runtime.shutdown()
guide_mesh = guide_source.data
bpy.data.objects.remove(guide_source, do_unlink=True)
bpy.data.meshes.remove(guide_mesh)
instant_settings.auto_guides = False
instant_settings.target_faces = 20000
instant_settings.deterministic = False
print("REMI_INSTALLED_AUTOMATIC_GUIDES_OK")
source = bpy.data.objects.get("Cube")
assert source is not None
source.select_set(False)
mesh_data = bpy.data.meshes.new("InstalledAlphaSourceMesh")
mesh_builder = bmesh.new()
bmesh.ops.create_cube(mesh_builder, size=2.0)
mesh_builder.faces.remove(max(mesh_builder.faces, key=lambda face: face.calc_center_median().z))
mesh_builder.to_mesh(mesh_data)
mesh_builder.free()
alpha_source = bpy.data.objects.new("InstalledAlphaSource", mesh_data)
bpy.context.collection.objects.link(alpha_source)
alpha_source.select_set(True)
bpy.context.view_layer.objects.active = alpha_source
settings = bpy.context.scene.remi_settings
settings.hole_repair_method = "ALPHA_WRAP"
settings.alpha_wrap_alpha_ratio = 0.1
settings.alpha_wrap_auto_scale = False
settings.alpha_wrap_patch_ratio = 0.006
settings.alpha_wrap_patch_rings = 2
repair = importlib.import_module(module_name + ".features.repair.service")
repaired, error, report = repair.create_candidate(alpha_source, settings)
assert not error and repaired is not None, error
assert report["guide_faces"] > 0 and report["patch_faces"] > 0, report
bpy.data.objects.remove(repaired, do_unlink=True)
bpy.data.objects.remove(alpha_source, do_unlink=True)
bpy.context.view_layer.objects.active = source
source.select_set(True)
print("REMI_INSTALLED_ALPHA_GUIDED_REPAIR_OK")
assert hasattr(bpy.ops.remi, "create_mesh")
assert hasattr(bpy.ops.remi, "cancel_mesh")
assert hasattr(bpy.ops.remi, "install_surface_tool")
assert bpy.context.scene.remi_generation.mesh_quality == "DENSE"
assert bpy.context.scene.remi_texture.bake_method == "OCTREE"
assert bpy.context.scene.remi_texture.ray_steps == "512"
from pathlib import Path
assert (Path(addon.__file__).parent / "integrations/splat_project/octree.py").is_file()
assert (Path(addon.__file__).parent / "integrations/splat_project/octree_native.py").is_file()
assert (Path(addon.__file__).parent / "integrations/splat_project/local_gaussian.py").is_file()
assert (Path(addon.__file__).parent / "integrations/splat_project/local_gaussian_native.py").is_file()
assert bpy.context.scene.remi_texture.match_distance > 0
assert hasattr(bpy.types, "Remi_PT_GenerationSettings")
assert bpy.ops.remi.start_session.poll(), "Installed Start Remi is disabled"
assert bpy.ops.remi.run_full_flow.poll(), "Installed Full Flow is disabled"
assert bpy.ops.remi.start_session() == {"FINISHED"}
assert bpy.context.window_manager.remi_session.active
session = importlib.import_module(module_name + ".workflow.session")
session.runtime.cancel(bpy.context)
assert bpy.ops.remi.start_session.poll()
assert bpy.ops.remi.run_full_flow.poll()
print("REMI_INSTALLED_MANUAL_START_SMOKE_OK")
flow = importlib.import_module(module_name + ".app.flow")
assert flow.selected_document(bpy.context)["actions"] == ["REMESH", "DECIMATE", "UV", "BAKE_ALL"]
flow_settings = bpy.context.scene.remi_flow
flow_settings.preset = "CURRENT"
flow.initialize_steps(flow_settings, flow.DEFAULT_ACTIONS)
settings = bpy.context.scene.remi_settings
settings.voxel_size = 0.25
settings.decimation_passes = 1
settings.target_percentage = 0.5
settings.bake_texture_size = 256
settings.bake_half_scale = False
assert settings.bake_recalc_normals is False
material = bpy.data.materials.new("InstalledFlowMaterial")
material.diffuse_color = (0.7, 0.15, 0.03, 1.0)
bpy.context.object.data.materials.append(material)
assert bpy.ops.remi.run_full_flow() == {"FINISHED"}
assert not bpy.context.window_manager.remi_session.active
assert len(bpy.context.scene.objects) == 1
assert "4 stages" in bpy.context.scene.remi_flow.last_result
assert bpy.context.object.data.uv_layers.active is not None
for suffix in ("diffuse", "roughness", "normal", "ao"):
    image = bpy.data.images.get("Cube_" + suffix)
    assert image is not None and image.size[:] == (256, 256), suffix
print("REMI_INSTALLED_FULL_FLOW_SMOKE_OK")
app = importlib.import_module(module_name + ".app.application").get_application()
before_faces = len(bpy.context.object.data.polygons)
app.session.begin(bpy.context, bpy.context.object)
settings.decimation_passes = 1
settings.target_percentage = 0.5
settings.decimation_with_texture = True
app.session.execute_action(bpy.context, app.features.require_action("DECIMATE"))
after_faces = len(app.session.object(bpy.context).data.polygons)
assert 0 < after_faces < before_faces, (before_faces, after_faces)
app.session.cancel(bpy.context)
print("REMI_INSTALLED_TEXTURED_DECIMATION_SMOKE_OK")
bpy.ops.preferences.addon_disable(module=module_name)
print("REMI_INSTALLED_EXTENSION_SMOKE_OK")
