"""
AutoRemesher external tool integration.
Wraps the `autoremesher` CLI: https://github.com/huxingyi/autoremesher
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

ENV_EXECUTABLE = "AUTOREMESHER_PATH"


def resolve_executable(configured_path: str = "") -> Path:
    """Find the autoremesher executable from (in priority order):
    1. User-configured path in addon preferences
    2. AUTOREMESHER_PATH environment variable
    3. System PATH
    4. /Applications/autoremesher.app (macOS)
    """
    configured = configured_path.strip()
    if configured:
        return _resolve_app_bundle(Path(configured))

    env_path = os.environ.get(ENV_EXECUTABLE, "").strip()
    if env_path:
        return _resolve_app_bundle(Path(env_path))

    exe = shutil.which("autoremesher") or shutil.which("autoremesher.exe")
    if exe:
        return _resolve_app_bundle(Path(exe))

    if sys.platform == "darwin":
        default_app = Path("/Applications/autoremesher.app")
        if default_app.is_dir():
            resolved = _resolve_app_bundle(default_app)
            if resolved != default_app:
                return resolved

    return Path()


def _resolve_app_bundle(path: Path) -> Path:
    """Resolve a macOS .app bundle to the actual executable inside Contents/MacOS/."""
    if not (path.name.endswith(".app") and path.is_dir()):
        return path
    macos_dir = path / "Contents" / "MacOS"
    if not macos_dir.is_dir():
        return path
    app_stem = path.stem
    for name in (app_stem, app_stem.lower(), "autoremesher"):
        candidate = macos_dir / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return path


def validate_executable(executable: Path) -> str:
    """Returns empty string if valid, or an error message."""
    path_str = str(executable).strip()
    if not path_str or path_str == ".":
        return (
            "AutoRemesher executable not configured. Set it in the panel, "
            f"the {ENV_EXECUTABLE} env var, or add it to PATH."
        )
    if executable.is_file():
        return ""
    if sys.platform == "darwin" and executable.name.endswith(".app") and executable.is_dir():
        return ""
    return f"AutoRemesher executable not found: {executable}"


def build_command(
    executable: Path,
    input_path: Path,
    output_path: Path,
    report_path: Path,
    *,
    target_quads: int,
    edge_scaling: float,
    sharp_edge: float,
    smooth_normal: float,
    adaptivity: float,
) -> list:
    return [
        str(executable),
        "-i",
        str(input_path),
        "-o",
        str(output_path),
        "--report",
        str(report_path),
        "--target-quads",
        str(target_quads),
        "--edge-scaling",
        str(edge_scaling),
        "--sharp-edge",
        str(sharp_edge),
        "--smooth-normal",
        str(smooth_normal),
        "--adaptivity",
        str(adaptivity),
    ]
