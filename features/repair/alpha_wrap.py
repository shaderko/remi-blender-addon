"""CGAL Alpha Wrap guide construction and selective patch extraction."""

import json
import os
from pathlib import Path
import subprocess

import bpy

from ...blender.mesh_exchange import export_ply, import_ply
from ...blender.mesh_objects import (
    apply_modifiers,
    duplicate_object,
    remove_mesh_object,
    world_bounds_diagonal,
)
from ...integrations import alpha_wrap
from ...workflow.disk import SessionDiskService
from .guided import (
    _compose_source_with_guide_patches as compose_source_with_guide_patches,
    _evaluated_world_surface as evaluated_world_surface,
    _guide_boundary_coverage as guide_boundary_coverage,
    _guide_patch_faces as guide_patch_faces,
)


def resolve_alpha_wrap(settings) -> tuple[Path, str]:
    """Find the CGAL helper, optionally building it once on demand."""
    executable = alpha_wrap.resolve_executable(settings.alpha_wrap_executable)
    error = alpha_wrap.validate_executable(executable)
    if error and settings.alpha_wrap_auto_build:
        result = alpha_wrap.build_helper()
        if result.get("success"):
            executable = Path(result["executable"])
            error = alpha_wrap.validate_executable(executable)
        else:
            error = result.get("error", error)
    if error:
        error = (
            f"{error}. Install CGAL and CMake (macOS: brew install cgal cmake; "
            "Ubuntu: apt install libcgal-dev cmake), then click Build Helper."
        )
    return executable, error


def create_alpha_wrap_guide(
    source: bpy.types.Object,
    settings,
    suffix: str = "_wrapped",
    alpha_ratio: float = None,
    disk=None,
) -> tuple:
    """Run compiled Alpha Wrapping and import its temporary watertight guide."""
    executable, error = resolve_alpha_wrap(settings)
    if error:
        return None, error, {}

    diagonal = world_bounds_diagonal(source)
    if diagonal <= 0.0:
        return None, "The source mesh has zero-size bounds", {}
    alpha = diagonal * float(
        settings.alpha_wrap_alpha_ratio if alpha_ratio is None else alpha_ratio
    )
    offset = min(
        diagonal * float(settings.alpha_wrap_offset_ratio),
        alpha * 0.95,
    )

    try:
        with SessionDiskService.operation_workspace(disk, "alpha-wrap") as workspace:
            input_path = str(workspace / "input.ply")
            output_path = str(workspace / "wrapped.ply")
            if not export_ply(source, input_path):
                return None, "Could not export the source mesh for Alpha Wrap", {}
            command = alpha_wrap.build_command(
                executable,
                input_path,
                output_path,
                alpha,
                offset,
            )
            process = subprocess.run(
                command,
                cwd=str(executable.parent),
                capture_output=True,
                text=True,
                check=False,
            )
            if process.returncode != 0:
                message = process.stderr.strip() or process.stdout.strip() or "Alpha Wrap failed"
                return None, message, {}
            if not os.path.isfile(output_path):
                return None, "Alpha Wrap completed without producing an output mesh", {}
            wrapped = import_ply(output_path)
            if not wrapped:
                return None, "Blender could not import the Alpha Wrap result", {}
            wrapped.name = source.name + suffix
            report = {}
            try:
                report = json.loads(process.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                pass
    except OSError as exc:
        return None, str(exc), {}

    bpy.ops.object.select_all(action="DESELECT")
    wrapped.select_set(True)
    bpy.context.view_layer.objects.active = wrapped
    return wrapped, "", report


def alpha_wrap_hole_patches(
    source: bpy.types.Object,
    settings,
    suffix: str = "_prepared",
    prepared=None,
    disk=None,
) -> tuple:
    """Keep the original mesh and add only gap-spanning Alpha Wrap faces."""
    source_bvh, boundary_points = evaluated_world_surface(source)
    if source_bvh is None:
        return None, "Could not build a surface index for the original mesh", {}
    if not boundary_points:
        prepared = prepared or duplicate_object(source, suffix)
        apply_modifiers(prepared)
        bpy.ops.object.select_all(action="DESELECT")
        prepared.select_set(True)
        bpy.context.view_layer.objects.active = prepared
        return prepared, "", {
            "patch_faces": 0,
            "guide_faces": 0,
            "detection_distance": 0.0,
            "boundary_coverage": 1.0,
            "chosen_alpha_ratio": 0.0,
            "guide_attempts": 0,
        }

    diagonal = world_bounds_diagonal(source)
    offset = diagonal * float(settings.alpha_wrap_offset_ratio)
    detection_distance = max(
        diagonal * float(settings.alpha_wrap_patch_ratio),
        offset * 2.5,
    )
    start_ratio = float(settings.alpha_wrap_alpha_ratio)
    max_ratio = max(start_ratio, float(settings.alpha_wrap_max_ratio))
    target_coverage = float(settings.alpha_wrap_coverage_target)
    candidate_ratio = start_ratio
    guide = None
    selected_faces = set()
    wrap_report = {}
    coverage = 0.0
    attempts = 0

    while True:
        attempts += 1
        candidate, error, candidate_report = create_alpha_wrap_guide(
            source,
            settings,
            "_alpha_guide",
            alpha_ratio=candidate_ratio,
            disk=disk,
        )
        if error:
            if guide:
                remove_mesh_object(guide)
            return None, error, {}
        candidate_faces = guide_patch_faces(
            candidate,
            source_bvh,
            detection_distance,
            int(settings.alpha_wrap_patch_rings),
        )
        candidate_coverage = guide_boundary_coverage(
            candidate,
            candidate_faces,
            boundary_points,
            max(detection_distance * 2.0, offset * 8.0),
        )
        if candidate_coverage >= coverage or guide is None:
            if guide:
                remove_mesh_object(guide)
            guide = candidate
            selected_faces = candidate_faces
            wrap_report = candidate_report
            coverage = candidate_coverage
            chosen_ratio = candidate_ratio
        else:
            remove_mesh_object(candidate)

        if (
            not settings.alpha_wrap_auto_scale
            or coverage >= target_coverage
            or candidate_ratio >= max_ratio - 1e-9
        ):
            break
        candidate_ratio = min(max_ratio, candidate_ratio * 1.65)

    guide_face_count = len(guide.data.polygons)
    if not selected_faces:
        remove_mesh_object(guide)
        return None, (
            "No hole-spanning guide faces were detected. Lower Hole Detection "
            "or increase Detail Scale so the guide bridges the openings."
        ), {}
    if settings.alpha_wrap_auto_scale and coverage < min(target_coverage, 0.50):
        failed_scale = chosen_ratio
        remove_mesh_object(guide)
        return None, (
            f"Hole preparation reached only {coverage:.0%} open-boundary coverage "
            f"at the maximum useful scale ({failed_scale:.3g}). Increase Maximum "
            "Scale or lower Hole Detection; the pipeline was stopped instead of "
            "silently producing an inadequately closed remesh."
        ), {}

    report = {
        "guide_faces": wrap_report.get("faces", guide_face_count),
        "boundary_coverage": coverage,
        "chosen_alpha_ratio": chosen_ratio,
        "guide_attempts": attempts,
    }
    return compose_source_with_guide_patches(
        source,
        guide,
        source_bvh,
        selected_faces,
        detection_distance,
        settings,
        suffix,
        report,
        source_boundary_points=boundary_points,
        prepared=prepared,
    )


_resolve_alpha_wrap = resolve_alpha_wrap
_create_alpha_wrap_guide = create_alpha_wrap_guide
_alpha_wrap_hole_patches = alpha_wrap_hole_patches
