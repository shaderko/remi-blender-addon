"""Build the Python-ABI-independent library at packaging time, never on import."""
from pathlib import Path
import argparse
import platform
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def library_name():
    return {"Darwin": "libremi_adaptive.dylib", "Linux": "libremi_adaptive.so",
            "Windows": "remi_adaptive.dll"}[platform.system()]


def build(output=None):
    output = Path(output or ROOT / "_native").resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="remi-adaptive-build-") as temporary:
        command = ["cmake", "-S", str(ROOT), "-B", temporary, "-DCMAKE_BUILD_TYPE=Release"]
        if platform.system() == "Windows":
            command += ["-A", "x64"]
        subprocess.run(command, check=True)
        subprocess.run(["cmake", "--build", temporary, "--config", "Release", "--parallel", "2"], check=True)
        subprocess.run(["cmake", "--install", temporary, "--config", "Release", "--prefix", str(output)], check=True)
    library = output / library_name()
    if platform.system() == "Darwin":
        subprocess.run(["codesign", "--force", "--sign", "-", str(library)], check=True)
    return library


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    print(build(parser.parse_args().output))
