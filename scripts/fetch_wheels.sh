#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WHEELS_DIR="$REPO_DIR/wheels"

PYMESHLAB_FILE="pymeshlab-2025.7.post1-cp313-cp313-macosx_11_0_arm64.whl"
PYMESHLAB_SHA256="841b1fa15fc76ab73e594f4bbfcbe339782b36be67d9387ff5aa5f4ca422e180"
PYMESHLAB_URL="https://files.pythonhosted.org/packages/a7/06/ef68bf08d23f44118ab846b93dd30eaf87ad85cc44b4f3e5ac8764e9bc9f/$PYMESHLAB_FILE"
PYMESHLAB_PATH="$WHEELS_DIR/$PYMESHLAB_FILE"

checksum() {
  shasum -a 256 "$1" | awk '{print $1}'
}

mkdir -p "$WHEELS_DIR"
if [[ -f "$PYMESHLAB_PATH" ]] && [[ "$(checksum "$PYMESHLAB_PATH")" == "$PYMESHLAB_SHA256" ]]; then
  chmod 0644 "$PYMESHLAB_PATH"
  echo "verified: $PYMESHLAB_PATH"
  exit 0
fi

temporary="$(mktemp "$WHEELS_DIR/.pymeshlab.XXXXXX.whl")"
trap 'rm -f "$temporary"' EXIT
curl --fail --location --retry 3 --output "$temporary" "$PYMESHLAB_URL"

actual="$(checksum "$temporary")"
if [[ "$actual" != "$PYMESHLAB_SHA256" ]]; then
  echo "PyMeshLab wheel checksum mismatch: expected $PYMESHLAB_SHA256, got $actual" >&2
  exit 1
fi

mv "$temporary" "$PYMESHLAB_PATH"
chmod 0644 "$PYMESHLAB_PATH"
trap - EXIT
echo "downloaded and verified: $PYMESHLAB_PATH"
