"""Geometry-only Cloud CLI. The add-on uses the same in-memory API."""
import argparse
from pathlib import Path

from splatmesh import load_splats, save_mesh_ply
from .backend import AdaptiveConfig, reconstruct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=512)
    args = parser.parse_args()
    if args.resolution > 512:
        parser.error("Cloud reconstruction supports resolutions up to 512")
    mesh, _, _ = reconstruct(load_splats(args.input), AdaptiveConfig(resolution=args.resolution))
    if not len(mesh.faces):
        raise ValueError("Adaptive mesher produced no surface")
    save_mesh_ply(mesh, args.output)


if __name__ == "__main__":
    main()
