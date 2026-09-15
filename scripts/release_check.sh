#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BLENDER_BIN="${1:-/Applications/Blender.app/Contents/MacOS/Blender}"
OUTPUT_DIR="${2:-$REPO_DIR/dist}"

TESTS=(
  "tests/blender_architecture_regression.py"
  "tests/blender_release_regression.py"
  "tests/blender_session_regression.py"
  "tests/blender_flow_regression.py"
  "tests/blender_edit_tools_regression.py"
  "tests/blender_instant_meshes_smoke.py"
  "tests/blender_instant_meshes_topology_regression.py"
  "tests/blender_uv_regression.py"
)

for test_file in "${TESTS[@]}"; do
  echo "Running $test_file"
  "$BLENDER_BIN" --background --factory-startup --python-exit-code 1 --python "$REPO_DIR/$test_file"
done

"$SCRIPT_DIR/build_extension.sh" "$BLENDER_BIN" "$OUTPUT_DIR"
VERSION="$(awk -F '"' '$1 ~ /^version = / { print $2; exit }' "$REPO_DIR/blender_manifest.toml")"
ARTIFACT="$OUTPUT_DIR/remi-$VERSION-macos-arm64.zip"
"$SCRIPT_DIR/installed_extension_smoke.sh" "$BLENDER_BIN" "$ARTIFACT"
