"""Packaged camera-aware Gaussian-to-mesh supervisor, outside Blender."""
import argparse
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remi_surface.common import status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--install-tool", action="store_true")
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--status-dir", type=Path)
    args = parser.parse_args()
    if args.install_tool:
        from remi_surface.tool import install_tool
        from remi_surface.common import write_json
        output = args.status_dir.resolve()
        try:
            executable = install_tool(args.folder, lambda phase: status(output, phase))
            write_json(output / "complete.json", {"schema": 1, "complete": True, "executable": str(executable)})
            status(output, "Surface tool installed and verified")
            return 0
        except Exception as exc:
            status(output, "Surface-tool installation failed", error=str(exc))
            traceback.print_exc()
            return 1
    if args.request is None:
        parser.error("--request is required")
    output = args.request.resolve().parent
    try:
        import json
        from remi_surface.pipeline import run
        run(json.loads(args.request.read_text(encoding="utf-8")), output)
        return 0
    except InterruptedError as exc:
        status(output, "Cancelled", error=str(exc), cancelled=True)
        return 2
    except Exception as exc:
        status(output, "Mesh reconstruction failed", error=str(exc))
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
