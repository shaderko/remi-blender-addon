"""Prepare the Windows development launcher's isolated, fast-starting profile.

Run headlessly with --factory-startup. The UI must subsequently launch WITHOUT
that flag, or Blender will reset the Tablet API to Automatic before opening its
first window and can wait on an unresponsive Wacom/WinTab driver again.
"""

import os
from pathlib import Path
import shutil

import bpy


def prepare_profile():
    configured = os.environ.get("BLENDER_USER_CONFIG")
    local = os.environ.get("LOCALAPPDATA")
    if not configured or not local or not bpy.app.background:
        raise RuntimeError("Only run this through the Windows UI sketch launcher")
    profile = Path(configured).resolve()
    sandbox = (Path(local) / "Remi" / "ui-sketch").resolve()
    if not profile.is_relative_to(sandbox) or profile == sandbox:
        raise RuntimeError("Refusing to overwrite the normal Blender preferences")
    if Path(bpy.utils.user_resource("CONFIG")).resolve() != profile:
        raise RuntimeError("Blender is not using the requested isolated profile")
    profile.mkdir(parents=True, exist_ok=True)
    roaming = os.environ.get("APPDATA")
    if roaming:
        version = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
        original = Path(roaming) / "Blender Foundation" / "Blender" / version / "config"
        # Carry over Remi's runtime paths and board arrangement, not installed
        # add-ons or userpref.blend. Never overwrite edits made in the sandbox.
        for relative in (Path("remi-generation.json"), Path("remi/board_layout.json")):
            source, target = original / relative, profile / relative
            if source.is_file() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        presets = original / "remi" / "flow-presets"
        if presets.is_dir():
            for source in presets.glob("*.json"):
                target = profile / "remi" / "flow-presets" / source.name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    bpy.context.preferences.inputs.tablet_api = "WINDOWS_INK"
    bpy.ops.wm.save_userpref()
    print(f"Remi sketch: isolated Windows Ink preferences prepared at {profile}", flush=True)


if __name__ == "__main__":
    prepare_profile()
