"""Packaged surface pipeline. No dependency on developer scripts or model downloads."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from .common import check_cancel, sha256, status, write_json
from .processes import terminate_tree
from .tool import EXECUTABLE_SHA256, TAG, verify_tool


def run_stage(command, log, output, phase, env=None):
    status(output, phase)
    check_cancel(output)
    with Path(log).open("wb") as stream:
        process = subprocess.Popen([str(part) for part in command], stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        last_step = None
        try:
            while process.poll() is None:
                check_cancel(output)
                if phase.startswith("Optimizing"):
                    with Path(log).open("rb") as reader:
                        reader.seek(max(0, Path(log).stat().st_size - 8192))
                        steps = re.findall(rb"step\s+(\d+)/(\d+)", reader.read())
                    if steps and steps[-1] != last_step:
                        last_step = steps[-1]
                        current, total = (int(value) for value in last_step)
                        status(output, f"Optimizing surface · {current:,}/{total:,}")
                time.sleep(0.15)
            if process.returncode:
                tail = "\n".join(Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-12:])
                raise RuntimeError(f"{phase.rstrip('…')} failed. See {log}\n{tail}")
        finally:
            if process.poll() is None:
                terminate_tree(process)
    check_cancel(output)


def run(request, output):
    output = Path(output).resolve()
    quality = request.get("quality", "DENSE")
    if quality not in {"DENSE", "FAST"}:
        raise ValueError("Unsupported reconstruction quality")
    archive = Path(request["archive"])
    if not archive.is_file():
        raise FileNotFoundError(archive)
    blender = Path(request["blender"])
    if not blender.is_file():
        raise ValueError("Blender executable not found")
    executable = verify_tool(request["tool_folder"])
    status(output, "Preparing surface reconstruction…")
    try:
        import torch
        import scipy
        import trimesh
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("External Python needs numpy, scipy, Pillow, trimesh, and torch; install them in the configured Python environment") from exc
    from .exchange import export_colmap, load_gaussians, write_gaussian_ply
    from .optimizer import optimizer_environment, parse_camera_probe, train_arguments, mesh_arguments, validate_camera_probe
    from .silhouette import constrain

    started = time.perf_counter()
    torch.set_num_threads(8)
    cancel = lambda: check_cancel(output)
    cancel()
    arrays = load_gaussians(archive)
    # The source is object data, never a stale generation PLY/cache. Keep this
    # independent snapshot with the result even when Blender removes its temp NPZ.
    source = output / "source.npz"
    if source.exists():
        raise ValueError("Reconstruction output already contains a source snapshot")
    shutil.copyfile(archive, source)
    source_hash = sha256(source)
    initial = write_gaussian_ply(output / "source.ply", arrays)
    env = optimizer_environment()
    views = output / "views"
    renderer = Path(__file__).resolve().parents[1] / "render_views.py"
    run_stage([blender, "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", renderer, "--", "--archive", source, "--output", views, "--status-dir", output],
              output / "render.log", output, "Rendering Gaussian views…")
    dataset = json.loads((views / "dataset.json").read_text(encoding="utf-8"))
    if dataset["source_sha256"] != source_hash or not (views / "render_complete.json").is_file():
        raise RuntimeError("Gaussian render snapshot is incomplete or changed")
    status(output, "Preparing calibrated surface cameras…")
    data = export_colmap(views, output / "calibrated", dataset, arrays, cancel)
    probe_command = train_arguments(executable, data, initial, output / "probe", "calibration")
    probe_path = output / "camera_probe.json"
    run_stage(probe_command, output / "camera_probe.log", output, "Checking camera calibration…",
              dict(env, SS_DUMP_CAMERAS=str(probe_path)))
    probe = parse_camera_probe(probe_path.read_text(encoding="utf-8"))
    validate_camera_probe(probe, dataset)
    write_json(output / "camera_calibration.json", probe)
    training_command = None
    if quality == "DENSE":
        training_command = train_arguments(executable, data, initial, output / "training", "fit")
        run_stage(training_command, output / "training.log", output, "Optimizing surface…", env)
        checkpoint = output / "training/fit"
    else:
        checkpoint = initial
    raw_dir = output / "raw"
    raw_dir.mkdir()
    extraction_command = mesh_arguments(executable, checkpoint, data, raw_dir / "mesh")
    run_stage(extraction_command, output / "extraction.log", output, "Extracting camera-aware surface…", env)
    raw_mesh = raw_dir / "mesh.ply"
    if not raw_mesh.is_file():
        raise RuntimeError("Surface tool did not produce the expected mesh")
    status(output, "Validating surface against rendered silhouettes…")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    filtered = constrain(views, raw_mesh, output / "constrained", device=device,
                         progress=lambda phase: status(output, phase), cancelled=cancel)
    cancel()
    mesh_path = output / "mesh.ply"
    shutil.copyfile(output / "constrained/mesh.ply", mesh_path)
    topology = filtered["after"]
    warnings = []
    if topology["components"] > 1:
        warnings.append(f"{topology['components']:,} separate components; inspect fine structures/floaters")
    if topology["boundary_edges"]:
        warnings.append(f"{topology['boundary_edges']:,} open boundary edges; not automatically holes")
    if topology["nonmanifold_edges"]:
        warnings.append(f"{topology['nonmanifold_edges']:,} non-manifold edges")
    report = {"schema": 2, "source": request.get("source", ""), "archive": str(source),
              "source_sha256": source_hash, "mesh": str(mesh_path), "mesh_sha256": sha256(mesh_path),
              "gaussians": len(arrays["positions"]), "vertices": topology["vertices"],
              "triangles": topology["triangles"], "quality": quality, "topology": topology,
              "warnings": warnings, "elapsed_seconds": time.perf_counter() - started,
              "mesher": "Camera-aware opacity surface + training-silhouette constraints",
              "surface_tool": {"version": TAG, "sha256": EXECUTABLE_SHA256},
              "training_command": training_command, "extraction_command": extraction_command,
              "silhouette_constraints": filtered, "device": device,
              "postprocessing": "No decimation, smoothing, hole filling, or component deletion by size; upstream vertex splits retained",
              "limitation": "A reconstructed surface, not a guarantee of manifoldness or true hidden geometry"}
    write_json(output / "report.json", report)
    cancel()
    write_json(output / "complete.json", {"schema": 1, "complete": True})
    status(output, "Complete", complete=True, vertices=topology["vertices"], triangles=topology["triangles"])
    return report
