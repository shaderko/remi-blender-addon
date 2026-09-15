#!/usr/bin/env bash
set -euo pipefail

BLENDER_BIN="${1:-/Applications/Blender.app/Contents/MacOS/Blender}"
ARTIFACT="${2:?Pass the extension zip to test}"

if [[ ! -x "$BLENDER_BIN" ]]; then
  echo "Blender executable not found: $BLENDER_BIN" >&2
  exit 1
fi
if [[ ! -f "$ARTIFACT" ]]; then
  echo "Extension archive not found: $ARTIFACT" >&2
  exit 1
fi

REMI_SMOKE_ROOT="$(mktemp -d /tmp/remi-extension-smoke.XXXXXX)"
trap 'rm -rf "$REMI_SMOKE_ROOT"' EXIT
export BLENDER_USER_CONFIG="$REMI_SMOKE_ROOT/config"
export BLENDER_USER_EXTENSIONS="$REMI_SMOKE_ROOT/extensions"
export PYTHONNOUSERSITE=1
export REMI_EXPECTED_VERSION
REMI_EXPECTED_VERSION="$(
  unzip -p "$ARTIFACT" blender_manifest.toml \
    | awk -F '"' '$1 ~ /^version = / { print $2; exit }'
)"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"

"$BLENDER_BIN" --command extension install-file \
  -r user_default \
  "$ARTIFACT"

"$BLENDER_BIN" --background --factory-startup --python-exit-code 1 --python-expr '
import bpy
import bmesh
import importlib
import os
import site

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
'
