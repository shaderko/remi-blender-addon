"""External AutoRemesher process service."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import bpy

from ... import autoremesher
from ...infrastructure.blender import mesh_exchange
from ...storage.disk import SessionDiskService


def create_candidate(obj, settings, disk=None):
    """Run the optional AutoRemesher executable and return its mesh candidate."""
    executable = autoremesher.resolve_executable(settings.autoremesher_executable)
    error = autoremesher.validate_executable(executable)
    if error:
        return None, error, {}

    with SessionDiskService.operation_workspace(disk, "autoremesher") as workspace:
        temp_dir = str(workspace)
        base_name = bpy.path.clean_name(obj.name)
        input_obj = os.path.join(temp_dir, f"{base_name}_input.obj")
        output_obj = os.path.join(temp_dir, f"{base_name}_output.obj")
        report_path = os.path.join(temp_dir, f"{base_name}_report.txt")
        if not mesh_exchange.export_obj_for_tool(obj, input_obj):
            return None, "OBJ export failed", {}

        command = autoremesher.build_command(
            executable,
            Path(input_obj),
            Path(output_obj),
            Path(report_path),
            target_quads=settings.ar_target_quads,
            edge_scaling=settings.ar_edge_scaling,
            sharp_edge=settings.ar_sharp_edge,
            smooth_normal=settings.ar_smooth_normal,
            adaptivity=settings.ar_adaptivity,
        )
        result = subprocess.run(
            command,
            cwd=str(executable.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            return None, message or "AutoRemesher failed", {"command": command}
        if not os.path.isfile(output_obj):
            return None, "AutoRemesher did not produce output file", {"command": command}

        candidate = mesh_exchange.import_obj_result(output_obj)
        if not candidate:
            return None, "Failed to import AutoRemesher result", {"command": command}
        candidate.name = obj.name + "_autoremesh"
        candidate.pop("_remi_session_id", None)
        return candidate, "", {"command": command}
