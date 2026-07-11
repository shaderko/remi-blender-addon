"""CGAL Alpha Wrap helper discovery, building, and command construction."""

import os
import platform
import shutil
import subprocess
from pathlib import Path


ENV_EXECUTABLE = "REMI_ALPHA_WRAP_PATH"


def _executable_name() -> str:
    return "remi_alpha_wrap.exe" if os.name == "nt" else "remi_alpha_wrap"


def _find_cmake() -> str:
    """Find CMake even when Blender was launched without the shell's PATH."""
    from_path = shutil.which("cmake")
    if from_path:
        return from_path
    candidates = [
        Path("/opt/homebrew/bin/cmake"),
        Path("/usr/local/bin/cmake"),
        Path("/usr/bin/cmake"),
    ]
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates.append(Path(program_files) / "CMake" / "bin" / "cmake.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def resolve_executable(configured_path: str = "") -> Path:
    candidates = []
    if configured_path.strip():
        candidates.append(Path(configured_path.strip()).expanduser())
    if os.environ.get(ENV_EXECUTABLE, "").strip():
        candidates.append(Path(os.environ[ENV_EXECUTABLE].strip()).expanduser())

    addon_dir = Path(__file__).resolve().parent
    system = platform.system().lower()
    machine = platform.machine().lower()
    candidates.extend([
        addon_dir / "bin" / f"{system}-{machine}" / _executable_name(),
        addon_dir / "alpha_wrap_helper" / "build" / _executable_name(),
        addon_dir / "alpha_wrap_helper" / "build" / "Release" / _executable_name(),
    ])
    from_path = shutil.which(_executable_name())
    if from_path:
        candidates.append(Path(from_path))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return Path()


def validate_executable(executable: Path) -> str:
    if not str(executable).strip() or str(executable) == ".":
        return "Alpha Wrap helper is not built or configured"
    if not executable.is_file():
        return f"Alpha Wrap helper not found: {executable}"
    if not os.access(executable, os.X_OK):
        return f"Alpha Wrap helper is not executable: {executable}"
    return ""


def build_helper() -> dict:
    source_dir = Path(__file__).resolve().parent / "alpha_wrap_helper"
    build_dir = source_dir / "build"
    cmake = _find_cmake()
    if not cmake:
        return {"success": False, "error": "CMake was not found"}
    try:
        configure_command = [
            cmake, "-S", str(source_dir), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release",
        ]
        if platform.system() == "Darwin":
            for prefix in (Path("/opt/homebrew"), Path("/usr/local")):
                if (prefix / "lib" / "cmake" / "CGAL").is_dir():
                    configure_command.append(f"-DCMAKE_PREFIX_PATH={prefix}")
                    break
        configure = subprocess.run(
            configure_command,
            capture_output=True,
            text=True,
            check=False,
        )
        if configure.returncode != 0:
            message = configure.stderr.strip() or configure.stdout.strip()
            return {
                "success": False,
                "error": message or "CMake configuration failed. Install CGAL development files first.",
            }
        build = subprocess.run(
            [cmake, "--build", str(build_dir), "--config", "Release", "--parallel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            return {"success": False, "error": build.stderr.strip() or build.stdout.strip() or "Build failed"}
        executable = resolve_executable("")
        error = validate_executable(executable)
        return {"success": not bool(error), "executable": str(executable), "error": error}
    except OSError as error:
        return {"success": False, "error": str(error)}


def build_command(
    executable: Path,
    input_path: str,
    output_path: str,
    alpha: float,
    offset: float,
) -> list[str]:
    return [
        str(executable),
        "--input", str(input_path),
        "--output", str(output_path),
        "--alpha", f"{alpha:.17g}",
        "--offset", f"{offset:.17g}",
    ]
