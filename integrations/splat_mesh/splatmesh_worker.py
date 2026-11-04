"""Splat-to-mesh worker backed by the remi-splat-mesh package.

Runs outside Blender, like worker.py, and emits the same schema-2 report so the
existing client can consume it unchanged.

Install the backend into the external Python first:
    pip install "remi-splat-mesh @ git+https://github.com/shaderko/remi-splat-mesh@v0.1.3"
"""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import check_cancel, sha256, status, write_json

BACKEND = "remi-splat-mesh"
REPOSITORY = "https://github.com/shaderko/remi-splat-mesh"
INSTALL_HINT = (
    'pip install "remi-splat-mesh @ '
    f'git+{REPOSITORY}@v0.1.3"'
)

QUALITY = {
    "DENSE": {"resolution": 512, "level": 0.05},
    "FAST": {"resolution": 256, "level": 0.05},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()

    request_path = args.request.resolve()
    output = request_path.parent
    request = json.loads(request_path.read_text(encoding="utf-8"))
    started = time.perf_counter()

    try:
        quality = request.get("quality", "DENSE")
        if quality not in QUALITY:
            raise ValueError("Unsupported reconstruction quality")

        archive = Path(request["archive"])
        if not archive.is_file():
            raise ValueError(f"Gaussian archive not found: {archive}")

        status(output, "Loading remi-splat-mesh")
        try:
            from splatmesh import (ExtractConfig, extract_mesh, load_splats,
                                   save_mesh_ply)
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", "splatmesh")
            if missing == "splatmesh":
                raise RuntimeError(
                    f"The {BACKEND} backend is not installed in this Python. Run: {INSTALL_HINT}"
                ) from exc
            raise RuntimeError(
                f"{BACKEND} needs numpy, scipy and scikit-image; missing '{missing}'. "
                f"Run: pip install numpy scipy scikit-image"
            ) from exc

        options = dict(QUALITY[quality])
        for key in ("resolution", "level"):
            if isinstance(request.get(key), (int, float)):
                options[key] = request[key]
        if isinstance(request.get("size_normalize"), bool):
            options["size_normalize"] = request["size_normalize"]
        config = ExtractConfig(**options)

        check_cancel(output)
        splats = load_splats(archive)
        status(output, f"Extracting surface from {len(splats):,} splats",
               gaussians=len(splats),
               resolution=config.resolution, level=config.level)

        def progress(fraction):
            check_cancel(output)
            status(output, f"Extracting surface {fraction:.0%}",
                   gaussians=len(splats), progress=round(fraction, 3))

        result = extract_mesh(splats, config, progress=progress)

        mesh_path = output / "mesh.ply"
        save_mesh_ply(result.mesh, mesh_path)
        check_cancel(output)

        report = result.report
        warnings = []
        if report.components > 1:
            warnings.append(f"{report.components:,} separate components; "
                            "inspect fine structures and floaters")
        if report.boundary_edges:
            warnings.append(f"{report.boundary_edges:,} open boundary edges")
        if report.nonmanifold_edges:
            warnings.append(f"{report.nonmanifold_edges:,} non-manifold edges")

        write_json(output / "report.json", {
            "schema": 2,
            "source": request.get("source", ""),
            "archive": str(archive),
            "source_sha256": sha256(archive),
            "mesh": str(mesh_path),
            "mesh_sha256": sha256(mesh_path),
            "gaussians": report.splats,
            "vertices": int(len(result.mesh.vertices)),
            "triangles": report.triangles,
            "quality": quality,
            "topology": {"vertices": int(len(result.mesh.vertices)),
                         "triangles": report.triangles,
                         "components": report.components,
                         "boundary_edges": report.boundary_edges,
                         "nonmanifold_edges": report.nonmanifold_edges},
            "warnings": warnings,
            "elapsed_seconds": time.perf_counter() - started,
            "mesher": f"{BACKEND} {getattr(sys.modules.get('splatmesh'), '__version__', '')}".strip(),
            "config": {
                "resolution": config.resolution,
                "level": config.level,
                "size_normalize": config.size_normalize,
                "min_component_faces": config.min_component_faces,
                "taubin_iterations": config.taubin_iterations,
                "min_splat_sigma_voxels": config.min_splat_sigma_voxels,
                "support_sigma": config.support_sigma,
                "max_radius": config.max_radius,
                "padding": config.padding,
            },
            "limitations": [
                "Iso-surface of the accumulated opacity field; not a guarantee of "
                "hidden geometry.",
                "Structure thinner than one voxel is not resolved.",
            ],
        })
        write_json(output / "complete.json", {"schema": 1, "complete": True})
        status(output, "Complete", complete=True, vertices=len(result.mesh.vertices),
               triangles=report.triangles)
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