#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON_BIN:-python3}" "$SCRIPT_DIR/release.py" smoke --blender "${1:-/Applications/Blender.app/Contents/MacOS/Blender}" --artifact "${2:?Pass the extension zip to test}"
