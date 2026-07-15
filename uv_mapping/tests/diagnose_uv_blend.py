"""Diagnose Remi UV overlap pairs on the active object of a loaded .blend."""

from pathlib import Path
import os
import sys

import bpy


ADDON_PARENT = Path(__file__).resolve().parents[3]
for module_name in tuple(sys.modules):
    if module_name == "remi" or module_name.startswith("remi."):
        del sys.modules[module_name]
if str(ADDON_PARENT) not in sys.path:
    sys.path.insert(0, str(ADDON_PARENT))

from remi.uv_mapping import ensure_remi_uv
from remi.uv_mapping.metrics import find_uv_overlaps


def main():
    obj = bpy.context.view_layer.objects.active
    if obj is None or obj.type != "MESH":
        raise RuntimeError("The loaded blend has no active mesh")
    print("DIAGNOSING", obj.name, len(obj.data.polygons), "faces")
    print("INITIAL OVERLAPS", len(find_uv_overlaps(obj.data)))
    margin_px = int(os.environ.get("REMI_UV_MARGIN_PX", "4"))
    print("PADDING", margin_px, "px")
    result = ensure_remi_uv(
        obj,
        profile_id="SCAN",
        texture_size=2048,
        margin_px=margin_px,
        preserve_existing_seams=False,
        replace_existing=True,
    )
    print("RESULT", result.success, result.error)
    print("SOLVER", result.solver)
    if result.stats is not None:
        summary = result.stats.to_dict()
        summary.pop("face_distortion", None)
        print("STATS", summary)
    print("WARNINGS", result.warnings)
    details = find_uv_overlaps(obj.data)
    print("FINAL OVERLAPS", len(details))
    for detail in details:
        print("OVERLAP", detail)


if __name__ == "__main__":
    main()
