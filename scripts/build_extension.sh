#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BLENDER_BIN="${1:-/Applications/Blender.app/Contents/MacOS/Blender}"
OUTPUT_DIR="${2:-$REPO_DIR/dist}"

if [[ ! -x "$BLENDER_BIN" ]]; then
  echo "Blender executable not found: $BLENDER_BIN" >&2
  exit 1
fi

VERSION="$(awk -F '"' '$1 ~ /^version = / { print $2; exit }' "$REPO_DIR/blender_manifest.toml")"
if [[ -z "$VERSION" ]]; then
  echo "Could not read the extension version from blender_manifest.toml" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
ARTIFACT="$OUTPUT_DIR/remi-$VERSION-macos-arm64.zip"
rm -f "$ARTIFACT"

"$SCRIPT_DIR/fetch_wheels.sh"

"$BLENDER_BIN" --command extension build \
  --source-dir "$REPO_DIR" \
  --output-filepath "$ARTIFACT"
"$BLENDER_BIN" --command extension validate "$ARTIFACT"

echo "$ARTIFACT"
