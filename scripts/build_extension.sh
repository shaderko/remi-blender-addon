#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON_BIN:-python3}" "$SCRIPT_DIR/release.py" build --blender "${1:-/Applications/Blender.app/Contents/MacOS/Blender}" --output-dir "${2:-$SCRIPT_DIR/../dist}"
