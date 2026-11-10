"""Launch the installable Remi board in Blender's main window.

    scripts\\run-ui-sketch.bat

The UI lives in features/board so installed extensions use the same code.
"""

import importlib.util
import os
from pathlib import Path
import sys

import bpy


def load_remi():
    for key in bpy.context.preferences.addons.keys():
        if key.lower() == "remi" or key.lower().endswith(".remi"):
            module = sys.modules.get(key)
            if module is not None and hasattr(module, "app"):
                return module
    if "remi" in sys.modules and hasattr(sys.modules["remi"], "app"):
        return sys.modules["remi"]
    root = Path(os.environ.get("REMI_ROOT") or Path(__file__).resolve().parents[1])
    spec = importlib.util.spec_from_file_location("remi", root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["remi"] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def open_remi():
    bpy.ops.remi_board.open()
    return None


def main():
    load_remi()
    bpy.app.timers.register(open_remi, first_interval=0.5)
    print("Remi: opening the docked generation workspace. Switch tabs to return to Layout.")


if __name__ == "__main__":
    main()
