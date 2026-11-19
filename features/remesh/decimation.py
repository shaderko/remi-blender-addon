"""MeshLab decimation service used by session and standalone adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import nullcontext

import bpy

from ...integrations import meshlab
from ...blender import mesh_exchange
from ...workflow.disk import SessionDiskService
from .textured_exchange import export_uv_mesh, restore_materials


def _run_textured_worker(input_path: str, output_path: str, settings) -> list[dict]:
    import pymeshlab

    worker = Path(__file__).resolve().parents[2] / "integrations" / "meshlab" / "worker.py"
    environment = os.environ.copy()
    private_site = str(Path(pymeshlab.__file__).resolve().parent.parent)
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (private_site, existing_pythonpath) if path
    )
    process = subprocess.run(
        [
            sys.executable,
            str(worker),
            input_path,
            output_path,
            str(settings.target_percentage),
            str(settings.decimation_passes),
            str(settings.decimation_preserve_detail),
            "true",
            str(settings.decimation_target_faces),
        ],
        capture_output=True,
        text=True,
        env=environment,
    )
    results = []
    for line in process.stdout.splitlines():
        try:
            status = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "pass" in status:
            results.append(
                {
                    "success": True,
                    "pass": status["pass"],
                    "input_faces": status.get("in_faces"),
                    "output_faces": status.get("out_faces"),
                }
            )
    if process.returncode != 0 or not os.path.isfile(output_path):
        error = (
            process.stderr.strip()
            or process.stdout.strip()
            or "Textured MeshLab decimation failed"
        )
        return [{"success": False, "pass": len(results) + 1, "error": error}]
    return results


def create_candidate(obj, settings, disk=None):
    """Run MeshLab on ``obj`` and return an imported transactional candidate."""
    if not meshlab.ensure_pymeshlab():
        return None, meshlab.pymeshlab_unavailable_message(), []
    keep_texture = settings.decimation_with_texture
    if keep_texture and not obj.data.uv_layers:
        return None, "Keep Texture needs a mesh with UVs", []

    with SessionDiskService.operation_workspace(disk, "decimate") as workspace:
        temp_dir = str(workspace)
        base_name = "mesh"  # ASCII exchange names also avoid VCG's MTL path parser bugs.
        file_ext = "obj" if keep_texture else "ply"
        input_path = os.path.join(temp_dir, f"{base_name}_input.{file_ext}")
        output_path = os.path.join(temp_dir, f"{base_name}_decimated.{file_ext}")
        try:
            with (export_uv_mesh(obj, input_path) if keep_texture else nullcontext()) as exchange:
                if not keep_texture and not mesh_exchange.export_ply(obj, input_path):
                    return None, "PLY export failed", []
                results = (
                    _run_textured_worker(input_path, output_path, settings)
                    if keep_texture
                    else meshlab.run_multi_pass_decimation(
                        input_path=input_path,
                        output_path=output_path,
                        passes=settings.decimation_passes,
                        target_faces=settings.decimation_target_faces,
                        target_percentage=settings.target_percentage,
                        preserve_detail=settings.decimation_preserve_detail,
                    )
                )
        except (ValueError, RuntimeError, OSError) as exc:
            return None, str(exc), []
        for result in results:
            if not result["success"]:
                return (
                    None,
                    f"Decimation pass {result['pass']} failed: {result.get('error')}",
                    results,
                )

        candidate = (
            mesh_exchange.import_obj_result(output_path)
            if keep_texture
            else mesh_exchange.import_ply(output_path)
        )
        if not candidate:
            kind = "textured OBJ" if keep_texture else "PLY"
            return None, f"Failed to import decimated {kind}", results
        if keep_texture:
            try:
                restore_materials(candidate, exchange["materials"])
                if not candidate.data.uv_layers.active:
                    raise RuntimeError("MeshLab returned no UV coordinates; source was kept")
                candidate.data.uv_layers.active.name = exchange["uv_name"]
                candidate.data.uv_layers.active.active_render = True
            except (ValueError, RuntimeError, KeyError) as exc:
                from ...blender.mesh_objects import remove_mesh_object
                remove_mesh_object(candidate)
                return None, str(exc), results
        elif obj.data.materials:
            # PLY carries vertex colors but not material slots. Preserve the
            # source material so reconstructed vertex-color meshes stay shaded
            # after decimation.
            candidate.data.materials.clear()
            for material in obj.data.materials:
                candidate.data.materials.append(material)
        candidate.name = obj.name + settings.output_name_suffix
        candidate.pop("_remi_session_id", None)
        return candidate, "", results


class DecimationService:
    """MeshLab-backed reduction use case."""

    def decimate(self, candidate, settings, *, disk=None):
        return create_candidate(candidate, settings, disk=disk)
