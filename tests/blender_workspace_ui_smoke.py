"""Optional real sidebar/popover screenshots in a separate factory-startup UI."""
import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--image", default="")
parser.add_argument("--output", type=Path)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
output = args.output or ROOT / "generations" / ("ui_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
output.mkdir(parents=True, exist_ok=False)
(output / "temporary").mkdir()
bpy.context.preferences.filepaths.temporary_directory = str((output / "temporary").resolve())
spec = importlib.util.spec_from_file_location("remi", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
remi = importlib.util.module_from_spec(spec)
sys.modules["remi"] = remi
spec.loader.exec_module(remi)
remi.register()
step = 0
area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")


def advance():
    global step
    if step == 0:
        area.spaces.active.show_region_ui = True
        bpy.context.scene.remi_generation.image = args.image
        area.tag_redraw()
    elif step == 1:
        region = next(region for region in area.regions if region.type == "UI")
        (output / "region.json").write_text(json.dumps({
            "x": region.x, "y": region.y, "width": region.width, "height": region.height,
        }), encoding="utf-8")
        area.tag_redraw()
    elif step == 2:
        bpy.ops.screen.screenshot(filepath=str(output / "native-tools.png"))
        bpy.ops.remi.select_workspace(workspace="OPTIMIZE")
    elif step == 3:
        bpy.ops.screen.screenshot(filepath=str(output / "optimize.png"))
        bpy.ops.remi.select_workspace(workspace="TEXTURE")
    elif step == 4:
        region = next(region for region in area.regions if region.type == "UI")
        bpy.context.window.cursor_warp(region.x + region.width - 55, region.y + region.height - 100)
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.wm.call_panel("INVOKE_DEFAULT", name="Remi_PT_GenerationSettings", keep_open=True)
    elif step == 5:
        bpy.ops.screen.screenshot(filepath=str(output / "settings.png"))
        (output / "ui_smoke_passed.txt").write_text("Optimize/Texture sidebar and board settings popover drawn successfully.\n")
        print(f"REMI_WORKSPACE_UI_SMOKE_OK: {output}", flush=True)
        bpy.ops.wm.quit_blender()
        return None
    step += 1
    return 1.0


def guarded():
    try:
        return advance()
    except Exception:
        traceback.print_exc()
        import os
        os._exit(1)


bpy.app.timers.register(guarded, first_interval=1.0)
