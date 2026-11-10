"""Optional UI screenshot: installed native mesh sidebar and board launcher."""
import importlib
from pathlib import Path
import sys

import bpy

module_name = "bl_ext.user_default.remi"
assert module_name in sys.modules
archive = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
out = Path(sys.argv[sys.argv.index("--") + 2]).resolve()
out.parent.mkdir(parents=True, exist_ok=True)
adapter = importlib.import_module(module_name + ".blender.gaussian_splats")
adapter.import_splats(bpy.context, archive, display="NATIVE")
bpy.context.window_manager.remi_ui.workspace = "OPTIMIZE"
area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
area.spaces.active.show_region_ui = True
area.tag_redraw()
bpy.ops.screen.screenshot(filepath=str(out))
print("UI SCREENSHOT", out, flush=True)
bpy.ops.wm.quit_blender()
