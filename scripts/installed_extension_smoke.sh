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
export REMI_EXPECTED_VERSION
REMI_EXPECTED_VERSION="$(
  unzip -p "$ARTIFACT" blender_manifest.toml \
    | awk -F '"' '$1 ~ /^version = / { print $2; exit }'
)"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"

"$BLENDER_BIN" --command extension install-file \
  -r user_default \
  "$ARTIFACT"

"$BLENDER_BIN" --background --factory-startup --python-expr '
import bpy
import importlib
import os

module_name = "bl_ext.user_default.remi"
result = bpy.ops.preferences.addon_enable(module=module_name)
assert result == {"FINISHED"}, result
addon = importlib.import_module(module_name)
version_core = os.environ["REMI_EXPECTED_VERSION"].split("-", 1)[0].split("+", 1)[0]
expected_version = tuple(int(part) for part in version_core.split("."))
assert addon.bl_info["version"] == expected_version, addon.bl_info["version"]
assert addon.__package__ == module_name, addon.__package__
bpy.ops.preferences.addon_disable(module=module_name)
print("REMI_INSTALLED_EXTENSION_SMOKE_OK")
'
