"""Exercise a clean installed extension; launched by scripts/release.py."""
import bpy
import bmesh
import importlib
import os
import site
import time
import json
from pathlib import Path
import tempfile

module_name = "bl_ext.user_default.remi"
result = bpy.ops.preferences.addon_enable(module=module_name)
assert result == {"FINISHED"}, result
addon = importlib.import_module(module_name)
version_core = os.environ["REMI_EXPECTED_VERSION"].split("-", 1)[0].split("+", 1)[0]
expected_version = tuple(int(part) for part in version_core.split("."))
assert addon.bl_info["version"] == expected_version, addon.bl_info["version"]
assert addon.__package__ == module_name, addon.__package__
assert addon.bl_info["blender"] == (5, 3, 0)
gaussians = importlib.import_module(module_name + ".blender.gaussian_splats")
assert gaussians.native_splats_available(), "Blender 5.3 native Gaussians are required"
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
embedded_sdk = importlib.import_module(module_name + ".integrations.remi_cloud.sdk")
cloud_adapter = importlib.import_module(module_name + ".integrations.remi_cloud.protocol")
cloud_requests = importlib.import_module(module_name + ".integrations.remi_cloud.jobs")
assert issubclass(cloud_adapter.CloudSession, embedded_sdk.CloudSession)
assert cloud_requests.CloudRequest is embedded_sdk.CloudRequest
assert importlib.import_module(module_name + ".features.cloud").Remi_PT_BoardCloud.bl_label == "Remi Account"
assert bpy.context.scene.remi_generation.backend == "LOCAL"
assert callable(importlib.import_module(module_name + ".features.cloud.generation").start_image)
print("REMI_INSTALLED_SHARED_CLOUD_SDK_OK")
print("REMI_INSTALLED_IRIS_IMAGES_OK")
codex_client = importlib.import_module(module_name + ".integrations.codex_images.client")
image_references = importlib.import_module(module_name + ".features.images.references")
assert bpy.context.scene.remi_image.provider == "DEFAULT"
assert {item.identifier for item in bpy.context.scene.remi_image.bl_rna.properties["provider"].enum_items} == {"DEFAULT", "CODEX"}
assert bpy.ops.remi.codex_sign_in.get_rna_type() and bpy.ops.remi.codex_sign_out.get_rna_type()
assert codex_client.session.process is None
assert image_references.capture(None) == []
assert (Path(addon.__file__).parent / "integrations/codex_images/worker.py").is_file()
print("REMI_INSTALLED_CODEX_CONTEXT_OK")
credential_vault = importlib.import_module(module_name + ".integrations.credentials.vault")
assert callable(credential_vault.Vault)
assert callable(importlib.import_module(module_name + ".integrations.remi_cloud.credentials").restore)
print("REMI_INSTALLED_PERSISTENT_LOGIN_OK")
idol_local = importlib.import_module(module_name + ".integrations.idol.runtime")
assert callable(idol_local.IdolGenerator)
assert "IDOL_LOCAL" in {item.identifier for item in bpy.context.scene.remi_generation.bl_rna.properties["engine"].enum_items}
print("REMI_INSTALLED_IDOL_ENGINE_OK")
board_canvas = importlib.import_module(module_name + ".features.board.canvas")
board_workspace = importlib.import_module(module_name + ".features.board.workspace")
board_toolbar = importlib.import_module(module_name + ".features.board.toolbar")
board_preview = importlib.import_module(module_name + ".features.board.splat_preview")
assert issubclass(board_preview.NativeMeshPreview, board_preview.NativePreview)
assert board_canvas.NativeMeshPreview is board_preview.NativeMeshPreview
assert board_canvas.gallery is image_panel.gallery
assert hasattr(bpy.ops.remi_board, "open")
assert board_canvas.BOARD is None  # Enabling must not rearrange a customer's screen.
assert not bpy.app.timers.is_registered(board_workspace.monitor_workspace)  # Headless.
for operator in ("create_mesh", "run_flow", "create_lods", "add_asset", "export_asset", "edit_mesh"):
    assert "asset_id" in getattr(bpy.ops.remi_board, operator).get_rna_type().properties
assert (Path(addon.__file__).parent / "features/board/local_worker.py").is_file()
board_editing = importlib.import_module(module_name + ".features.board.editing")
board_native = importlib.import_module(module_name + ".features.board.native_assets")
assert callable(board_native.write) and callable(board_native.read)
assert "return_to_board" in bpy.ops.remi_board.apply_edit.get_rna_type().properties
assert bpy.ops.remi_board.return_to_board.get_rna_type()
assert bpy.context.window_manager.remi_ui.workspace == "OPTIMIZE"
assert {item.identifier for item in bpy.context.window_manager.remi_ui.bl_rna.properties["workspace"].enum_items} == {"OPTIMIZE", "TEXTURE"}
assert not hasattr(board_editing, "draw_edit_controls")
board_effects = importlib.import_module(module_name + ".features.board.texture_effects")
assert callable(board_canvas.queue_texture) and callable(board_effects.draw_working_card)
assert board_canvas.toolbar is board_toolbar
assert [tool.id for tool in board_toolbar.TOOLS] == [
    "generate3d", "mesh", "flow", "texture", "lods", "editmesh", "add3d", "import", "addobject", "export", "cancel",
]
assert {tool.id for tool in board_toolbar.TOOLS if tool.settings} == {"generate3d", "mesh", "flow", "texture", "lods"}
assert {"action", "asset_id"} <= set(bpy.ops.remi_board.tool_action.get_rna_type().properties.keys())
assert "tool" in bpy.ops.remi_board.tool_settings.get_rna_type().properties
assert not hasattr(board_canvas, "primary_action")
print("REMI_INSTALLED_BOARD_TOOLBAR_OK")
board_organization = importlib.import_module(module_name + ".features.board.organization")
board_organizing = importlib.import_module(module_name + ".features.board.organizing")
for operator in ("rename", "group", "ungroup", "move_to_group"):
    assert getattr(bpy.ops.remi_board, operator).get_rna_type()
model = board_organization.Organization()
child = model.create({"a", "b"}, title="Parts")
parent = model.create({"c"}, {child.id}, title="Assembly")
assert model.members(parent.id) == {"a", "b", "c"} and child.parent == parent.id
assert callable(board_organizing.handle_event) and callable(board_organization.place_in_view)
print("REMI_INSTALLED_BOARD_ORGANIZATION_OK")
board_layers = importlib.import_module(module_name + ".features.board.layers")
assert bpy.ops.remi_board.reveal_layer.get_rna_type()
assert bpy.ops.remi_board.layer_fold.get_rna_type()
assert bpy.ops.remi_board.layer_move.get_rna_type()
assert "query" in bpy.context.window_manager.remi_board_layers.bl_rna.properties
assert board_layers.REMI_BOARD_PT_layers.bl_space_type == "IMAGE_EDITOR"
assert board_layers.REMI_BOARD_PT_layers.bl_region_type == "UI"
assert not board_layers.REMI_BOARD_PT_layers.poll(bpy.context)
assert callable(board_organization.layer_rows) and callable(board_layers.thumbnail_icon)
assert callable(board_organizing.drop_target) and callable(board_organizing.apply_drop)
print("REMI_INSTALLED_BOARD_LAYERS_OK")
print("REMI_INSTALLED_DOCKED_WORKSPACE_OK")
cloud_jobs=importlib.import_module(module_name+".integrations.remi_cloud.jobs")
cloud_controls=importlib.import_module(module_name+".features.cloud.jobs")
assert cloud_jobs.CloudRequest is not None
assert not bpy.context.window_manager.remi_cloud.signed_in
assert not cloud_controls.Remi_OT_CloudGenerate.poll(bpy.context)
assert bpy.ops.remi.cloud_generate.get_rna_type() is not None
assert bpy.ops.remi.cloud_resume.get_rna_type() is not None
print("REMI_INSTALLED_CLOUD_CLIENT_OK")
instant_native = importlib.import_module(module_name + ".features.retopology.instant_meshes._native")
assert instant_native.Session is not None
assert instant_native.GUIDE_API_VERSION == 1
uv_engine = importlib.import_module(module_name + ".features.uv.engine")
uv_native = importlib.import_module(module_name + ".features.uv.engine._native")
assert os.path.realpath(uv_native.__file__).startswith(os.path.realpath(os.environ["BLENDER_USER_EXTENSIONS"]) + os.sep)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
with tempfile.TemporaryDirectory(prefix="remi-installed-quads-") as directory:
    native_path = board_native.write(bpy.context, [bpy.context.object], Path(directory) / "quads.blend")
    appended = board_editing.import_working_mesh(bpy.context, native_path)
    assert len(appended) == 1 and len(appended[0].data.vertices) == 8
    assert len(appended[0].data.polygons) == 6 and all(len(p.vertices) == 4 for p in appended[0].data.polygons)
    bpy.data.objects.remove(appended[0], do_unlink=True)
    bpy.context.view_layer.objects.active = bpy.data.objects["Cube"]
    bpy.context.active_object.select_set(True)
print("REMI_INSTALLED_NATIVE_QUADS_OK")
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
assert not instant_settings.adaptive_quads
assert instant_settings.adaptive_method == "SURFACE"
assert instant_settings.stitch_render_seams and instant_settings.adaptive_max_seconds == 60
assert instant_settings.adaptive_face_limit == 25000
assert callable(importlib.import_module(module_name + ".features.retopology.instant_meshes.adaptive").QuadSimplifier)
assert callable(importlib.import_module(module_name + ".features.retopology.instant_meshes.surface_adaptive").SurfacePatchRemesher)
instant_settings.target_faces = 1000
instant_settings.deterministic = True
instant_settings.preserve_creases = False
instant_settings.adaptive_quads = True
bpy.ops.mesh.primitive_plane_add(size=2)
surface_source = bpy.context.object
surface_object_count = len(bpy.context.scene.objects)
instant_runtime.start(surface_source, instant_settings)
deadline = time.monotonic() + 60
while instant_runtime.solve_stage or instant_runtime.busy:
    assert time.monotonic() < deadline, instant_runtime.last_error
    instant_runtime.poll()
    time.sleep(.001)
assert not instant_runtime.last_error, instant_runtime.last_error
assert instant_runtime.adaptive_report["rebuilt"], instant_runtime.adaptive_report
assert len(instant_runtime.preview[0]) == 4 and len(instant_runtime.preview[1]) == 1
assert len(surface_source.data.vertices) == 4 and len(bpy.context.scene.objects) == surface_object_count
instant_runtime.shutdown()
surface_mesh = surface_source.data
bpy.data.objects.remove(surface_source, do_unlink=True)
bpy.data.meshes.remove(surface_mesh)
print("REMI_INSTALLED_PLANAR_SURFACE_REBUILD_OK")
# The curved route must solve and validate the field layout from the installed
# ZIP, not merely import a module or reuse the native comparison.
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4)
organic_source = bpy.context.object
organic_vertices = [tuple(vertex.co) for vertex in organic_source.data.vertices]
instant_settings.adaptive_error = .01
instant_runtime.start(organic_source, instant_settings)
deadline = time.monotonic()+90
while instant_runtime.solve_stage or instant_runtime.busy:
    assert time.monotonic() < deadline, instant_runtime.last_error
    delay = instant_runtime.poll()
    time.sleep(delay if delay is not None else .001)
assert not instant_runtime.last_error, instant_runtime.last_error
assert instant_runtime.adaptive_report["rebuilt"], instant_runtime.adaptive_report
assert instant_runtime.adaptive_report["backend"] == "FIELD_ALIGNED"
assert instant_runtime.adaptive_report["ideal_valence_fraction"] >= .90
assert instant_runtime.adaptive_report["poles"] <= max(6, instant_runtime.adaptive_report["output_faces"]*.08)
assert instant_runtime.preview_quality["quad_fraction"] == 1
assert instant_runtime.preview_quality["boundary_edges"] == instant_runtime.preview_quality["nonmanifold_edges"] == 0
assert instant_runtime.can_accept
assert [tuple(vertex.co) for vertex in organic_source.data.vertices] == organic_vertices
instant_runtime.shutdown()
organic_mesh = organic_source.data
bpy.data.objects.remove(organic_source, do_unlink=True)
bpy.data.meshes.remove(organic_mesh)
instant_settings.adaptive_quads = False
instant_settings.preserve_creases = True
print("REMI_INSTALLED_SURFACE_REBUILD_OK")
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
# Exercise the packaged file-in/file-out worker, not just checkout imports.
board_jobs = importlib.import_module(module_name + ".features.board.jobs")
with tempfile.TemporaryDirectory(prefix="remi-installed-board-") as temporary:
    directory = Path(temporary)
    source_path = directory / "source.glb"
    bpy.ops.export_scene.gltf(filepath=str(source_path), export_format="GLB", use_selection=True)
    before_objects = [(obj.name, obj.data.as_pointer(), obj.select_get()) for obj in bpy.context.scene.objects]
    before_active = bpy.context.active_object
    job = board_jobs.LocalBlenderJob.start(output=directory, request={
        "operation": "FLOW", "input": str(source_path), "flow": {
            "version": 1, "name": "Installed Board UV", "actions": ["DECIMATE", "UV"],
            "settings": {"bake_texture_size": 256, "decimation_passes": 1,
                         "target_percentage": 0.75, "decimation_with_texture": False}}})
    deadline = time.monotonic() + 120
    while job.poll() is None:
        if time.monotonic() > deadline:
            job.cancel()
            raise AssertionError("Installed board worker timed out")
        time.sleep(0.05)
    try:
        artifact = job.result()
    except Exception:
        print((job.directory / "worker.log").read_text(encoding="utf-8", errors="replace"))
        raise
    assert artifact.suffix == ".glb" and artifact.is_file()
    result = json.loads((job.directory / "result.json").read_text(encoding="utf-8"))
    assert Path(result["preview"]).is_file(), result
    assert before_objects == [(obj.name, obj.data.as_pointer(), obj.select_get()) for obj in bpy.context.scene.objects]
    assert bpy.context.active_object == before_active
    # The installed bridge must also work without a mounted Image Editor.
    assets = importlib.import_module(module_name + ".features.board.assets")
    bpy.context.scene.remi_image.output = str(directory)
    parent = assets.Asset("installed-mesh", "MESH", artifact=str(source_path), title="Installed source")
    bpy.context.scene[assets.SCENE_ASSETS_KEY] = json.dumps({
        "gallery": board_canvas.layout_key(bpy.context), "assets": [parent.document()]})
    source_bytes = source_path.read_bytes()
    owner = bpy.context.scene
    edit_scene = board_editing.open_edit_scene(bpy.context, parent.id)
    with bpy.context.temp_override(scene=edit_scene, view_layer=edit_scene.view_layers[0]):
        working = bpy.context.view_layer.objects.active
        assert working != before_active and assets.OBJECT_ASSET_KEY not in working
        assert before_active.name not in edit_scene.objects
        version = board_editing.apply_to_card(bpy.context)
    assert version.id == parent.id and version.revisions[0]["artifact"] == str(source_path)
    assert Path(version.artifact).is_file() and source_path.read_bytes() == source_bytes
    assert not bpy.context.window_manager.remi_session.active
    assert bpy.data.objects.get(before_active.name) == before_active
    assert version.id in {item["id"] for item in json.loads(owner[assets.SCENE_ASSETS_KEY])["assets"]}
    bpy.context.window.scene = owner
print("REMI_INSTALLED_BOARD_EDITING_OK")
print("REMI_INSTALLED_BOARD_WORKER_OK")
bpy.ops.mesh.primitive_grid_add(x_subdivisions=16, y_subdivisions=16, size=2)
grid = bpy.context.object
image = bpy.data.images.new("Installed original albedo", 256, 256)
image.generated_color = (.3, .6, .9, 1)
material = bpy.data.materials.new("Installed original LOD material")
material.use_nodes = True
tex = material.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = image
material.node_tree.links.new(tex.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
grid.data.materials.append(material)
images_before, materials_before = set(bpy.data.images), set(bpy.data.materials)
lod_module = importlib.import_module(module_name + ".features.remesh.lods")
result = lod_module.create_lods(grid, levels=2, ratio=.5)
assert len(result["objects"]) == 2
assert all(obj.data.materials[0] is material for obj in result["objects"])
assert set(bpy.data.images) == images_before and set(bpy.data.materials) == materials_before
assert result["levels"][2]["triangles"] < result["levels"][1]["triangles"]
assert not hasattr(bpy.context.scene.remi_settings, "lod_texture_size")
assert not hasattr(bpy.context.scene.remi_settings, "lod_output")
print("REMI_INSTALLED_SHARED_LODS_OK")
bpy.ops.preferences.addon_disable(module=module_name)
print("REMI_INSTALLED_EXTENSION_SMOKE_OK")
