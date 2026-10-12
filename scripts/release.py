#!/usr/bin/env python3
"""Build and verify host-specific Remi extensions (Python 3.11+)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NATIVE_MODULES = {
    "features/retopology/instant_meshes": "_remi_instant_meshes",
    "features/uv/engine": "_remi_uv_packer",
}
TESTS = (
    "blender_architecture_regression.py",
    "blender_release_regression.py",
    "blender_session_regression.py",
    "blender_flow_regression.py",
    "blender_edit_tools_regression.py",
    "blender_instant_meshes_smoke.py",
    "blender_instant_meshes_topology_regression.py",
    "blender_uv_regression.py",
    "blender_bake_material_slots_regression.py",
    "blender_target_decimation_regression.py",
    "blender_uv_small_faces_regression.py",
    "blender_voxel_surface_regression.py",
)


def host_platform():
    system, machine = platform.system(), platform.machine().lower()
    if system == "Darwin" and machine == "arm64":
        return "macos-arm64"
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        return "windows-x64"
    if system == "Linux" and machine == "x86_64":
        return "linux-x64"
    raise RuntimeError(f"Unsupported release host: {system} {machine}")


def manifest():
    return tomllib.loads((ROOT / "blender_manifest.toml").read_text(encoding="utf-8"))


def wheel_spec(target):
    lock = json.loads((ROOT / "wheels" / "lock.json").read_text(encoding="utf-8"))
    spec = lock[target]
    if f'./wheels/{spec["filename"]}' not in manifest()["wheels"]:
        raise RuntimeError("Wheel lock and extension manifest disagree")
    return spec


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch_wheel(target):
    spec = wheel_spec(target)
    destination = ROOT / "wheels" / spec["filename"]
    if destination.is_file() and digest(destination) == spec["sha256"]:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pymeshlab-", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with urllib.request.urlopen(spec["url"], timeout=120) as response:
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
        if digest(temporary) != spec["sha256"]:
            raise RuntimeError(f"PyMeshLab checksum mismatch: {destination.name}")
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def run(*command, **kwargs):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run([str(arg) for arg in command], check=True, **kwargs)


def build_native(target):
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Build native modules with CPython 3.13 (Blender's Python ABI)")
    import pybind11

    # Build in a fresh directory so a previous compiler/architecture cannot leak in.
    with tempfile.TemporaryDirectory(prefix="remi-native-") as temporary:
        temporary = Path(temporary)
        install = temporary / "install"
        for directory, module in NATIVE_MODULES.items():
            build = temporary / module
            options = ["-A", "x64"] if target == "windows-x64" else []
            run("cmake", "-S", ROOT / directory / "native", "-B", build,
                f"-DPython_EXECUTABLE={sys.executable}",
                f"-Dpybind11_DIR={pybind11.get_cmake_dir()}",
                "-DCMAKE_BUILD_TYPE=Release", *options)
            jobs = int(os.environ.get("REMI_BUILD_JOBS", "4"))
            if not 1 <= jobs <= 64:
                raise RuntimeError("REMI_BUILD_JOBS must be between 1 and 64")
            run("cmake", "--build", build, "--config", "Release", "--parallel", str(jobs))
            run("cmake", "--install", build, "--config", "Release", "--prefix", install)
        for directory, module in NATIVE_MODULES.items():
            source = native_file(install, directory, module, target)
            destination = ROOT / directory / "_native" / source.name
            replacement = destination.with_name(destination.name + ".new")
            shutil.copy2(source, replacement)
            if target == "macos-arm64":
                run("codesign", "--force", "--sign", "-", replacement)
            replacement.replace(destination)


def native_file(root, directory, module, target):
    suffix = {"windows-x64": ".cp313-win_amd64.pyd",
              "macos-arm64": ".cpython-313-darwin.so",
              "linux-x64": ".cpython-313-x86_64-linux-gnu.so"}[target]
    path = root / directory / "_native" / (module + suffix)
    if not path.is_file():
        raise RuntimeError(f"Missing {target} native module: {path}. Run the native command first.")
    return path


def stage_extension(stage, target, wheel):
    native = [native_file(ROOT, directory, module, target)
              for directory, module in NATIVE_MODULES.items()]
    # Never include another platform's binary, build caches, tests, or developer files.
    shutil.copytree(ROOT, stage, ignore=shutil.ignore_patterns(
        ".*", "__pycache__", "*.pyc", "*.so", "*.pyd", "*.whl", "*.zip",
        "dist", "scripts", "tests", "native", "weppy-project-sync"))
    for source in native:
        shutil.copy2(source, stage / source.relative_to(ROOT))
    shutil.copy2(wheel, stage / "wheels" / wheel.name)
    content = (ROOT / "blender_manifest.toml").read_text(encoding="utf-8")
    content = re.sub(r"(?m)^platforms = \[[^]]*\]", f'platforms = ["{target}"]', content)
    content = re.sub(r"(?m)^wheels = \[[^]]*\]", f'wheels = ["./wheels/{wheel.name}"]', content)
    (stage / "blender_manifest.toml").write_text(content, encoding="utf-8")


def verify_archive(artifact, target):
    expected_native = {
        native_file(ROOT, directory, module, target).relative_to(ROOT).as_posix()
        for directory, module in NATIVE_MODULES.items()
    }
    with zipfile.ZipFile(artifact) as archive:
        data = tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))
        expected_wheel = "wheels/" + wheel_spec(target)["filename"]
        if data["platforms"] != [target] or data["wheels"] != ["./" + expected_wheel]:
            raise RuntimeError("Archive manifest does not match target platform")
        native = {name for name in archive.namelist() if name.endswith((".so", ".pyd"))}
        wheels = {name for name in archive.namelist() if name.endswith(".whl")}
        if native != expected_native or wheels != {expected_wheel}:
            raise RuntimeError("Archive contains missing or foreign platform binaries")
        if hashlib.sha256(archive.read(expected_wheel)).hexdigest() != wheel_spec(target)["sha256"]:
            raise RuntimeError("Archive wheel checksum mismatch")


def build_extension(blender, output, target):
    wheel = fetch_wheel(target)
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / f'remi-{manifest()["version"]}-{target}.zip'
    with tempfile.TemporaryDirectory(prefix="remi-package-") as temporary:
        stage = Path(temporary) / "remi"
        stage_extension(stage, target, wheel)
        built = Path(temporary) / artifact.name
        run(blender, "--command", "extension", "build", "--source-dir", stage,
            "--output-filepath", built)
        run(blender, "--command", "extension", "validate", built)
        verify_archive(built, target)
        shutil.copy2(built, artifact)
    print(artifact, flush=True)
    return artifact


def installed_smoke(blender, artifact):
    with zipfile.ZipFile(artifact) as archive:
        version = tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))["version"]
    with tempfile.TemporaryDirectory(prefix="remi-extension-smoke-") as temporary:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment.update(
            BLENDER_USER_CONFIG=str(Path(temporary) / "config"),
            BLENDER_USER_EXTENSIONS=str(Path(temporary) / "extensions"),
            PYTHONNOUSERSITE="1", REMI_EXPECTED_VERSION=version,
        )
        for key in ("BLENDER_USER_CONFIG", "BLENDER_USER_EXTENSIONS"):
            Path(environment[key]).mkdir()
        run(blender, "--command", "extension", "install-file", "-r", "user_default", artifact,
            env=environment)
        run(blender, "--background", "--factory-startup", "--python-exit-code", "1",
            "--python", ROOT / "tests" / "blender_installed_extension_smoke.py", env=environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "native", "build", "smoke", "check"))
    parser.add_argument("--blender", default=shutil.which("blender") or
                        "/Applications/Blender.app/Contents/MacOS/Blender")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args()
    target = host_platform()
    if args.command == "fetch":
        print(fetch_wheel(target))
    elif args.command == "native":
        build_native(target)
    elif args.command == "smoke":
        if args.artifact is None:
            parser.error("smoke requires --artifact")
        installed_smoke(args.blender, args.artifact.resolve())
    else:
        if args.command == "check":
            run(sys.executable, "-m", "unittest", "discover", "-s", ROOT / "tests",
                "-p", "test_platform_support.py")
            for test in TESTS:
                if test == "blender_target_decimation_regression.py":
                    # Exercise the actual target reducer with the same pinned
                    # dependency shipped to users, without a runtime pip install.
                    with tempfile.TemporaryDirectory(prefix="remi-test-wheel-") as temporary:
                        with zipfile.ZipFile(fetch_wheel(target)) as archive:
                            archive.extractall(temporary)
                        environment = os.environ.copy()
                        environment["REMI_TEST_DEPENDENCIES"] = temporary
                        run(args.blender, "--background", "--factory-startup", "--python-exit-code", "1",
                            "--python", ROOT / "tests" / test, env=environment)
                else:
                    run(args.blender, "--background", "--factory-startup", "--python-exit-code", "1",
                        "--python", ROOT / "tests" / test)
        artifact = build_extension(args.blender, args.output_dir.resolve(), target)
        if args.command == "check":
            installed_smoke(args.blender, artifact)


if __name__ == "__main__":
    main()
