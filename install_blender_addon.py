#!/usr/bin/env python3
"""
Install the Remi Blender addon.
This script copies the addon files to Blender's addon directory.

Usage:
    python3 install_blender_addon.py [--blender-version 5.1]
"""

import os
import sys
import shutil
import argparse
import platform


def get_blender_addon_dir(blender_version: str = None) -> str:
    """Get Blender's user addon directory."""
    system = platform.system()

    if system == "Darwin":  # macOS
        base = os.path.expanduser("~/Library/Application Support/Blender")
    elif system == "Linux":
        base = os.path.expanduser("~/.config/blender")
    elif system == "Windows":
        base = os.path.join(os.environ.get("APPDATA", ""), "Blender Foundation", "Blender")
    else:
        raise OSError(f"Unsupported platform: {system}")

    if not os.path.isdir(base):
        raise FileNotFoundError(f"Blender config directory not found: {base}")

    if blender_version:
        return os.path.join(base, blender_version, "scripts", "addons")

    # Auto-detect latest version
    versions = sorted(
        [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))],
        reverse=True,
    )
    for v in versions:
        addon_dir = os.path.join(base, v, "scripts", "addons")
        if os.path.isdir(addon_dir):
            return addon_dir

    raise FileNotFoundError(f"No Blender version found in {base}")


def main():
    parser = argparse.ArgumentParser(description="Install Remi Blender addon")
    parser.add_argument("--blender-version", help="Blender version (e.g., 5.1)")
    args = parser.parse_args()

    # Find addon directory
    addon_dir = get_blender_addon_dir(args.blender_version)
    target_dir = os.path.join(addon_dir, "remi")

    # Source dir is the directory containing this script
    src_dir = os.path.dirname(os.path.abspath(__file__))

    # Remove existing if present
    if os.path.isdir(target_dir):
        print(f"Removing existing installation at: {target_dir}")
        shutil.rmtree(target_dir)

    # Copy files (exclude install script and .git)
    os.makedirs(target_dir, exist_ok=True)
    for item in os.listdir(src_dir):
        if item in (".git", "install_blender_addon.py", "__pycache__"):
            continue
        s = os.path.join(src_dir, item)
        d = os.path.join(target_dir, item)
        if os.path.isdir(s):
            shutil.copytree(
                s,
                d,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "build",
                    "*.pyc",
                    "*.egg-info",
                ),
            )
        else:
            shutil.copy2(s, d)

    print(f"✓ Remi addon installed to: {target_dir}")
    print("  Restart Blender and enable it in Edit → Preferences → Add-ons → Remi")


if __name__ == "__main__":
    main()
