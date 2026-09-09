"""MeshLab decimation service used by session and standalone adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import bpy

from ... import meshlab_wrapper as meshlab
from ...infrastructure.blender import mesh_exchange
from ...workflow.disk_service import SessionDiskService


def _has_image_texture(obj: bpy.types.Object) -> bool:
    if not obj.data.uv_layers:
        return False
    for slot in obj.material_slots:
        material = slot.material
        if not material or not material.use_nodes or not material.node_tree:
            continue
        if any(
            node.type == "TEX_IMAGE" and node.image
            for node in material.node_tree.nodes
        ):
            return True
    return False


def _restore_texture_export_images(states: list[tuple]) -> None:
    for image, filepath_raw, file_format in states:
        image.filepath_raw = filepath_raw
        image.file_format = file_format


def _prepare_texture_export_images(
    obj: bpy.types.Object,
    temp_dir: str,
    base_name: str,
) -> tuple[list[tuple], list[str]] | None:
    states = []
    temp_files = []
    seen_images = set()
    image_number = 0
    for slot in obj.material_slots:
        material = slot.material
        if not material or not material.use_nodes or not material.node_tree:
            continue
        for node in material.node_tree.nodes:
            image = node.image if node.type == "TEX_IMAGE" else None
            if not image or image.as_pointer() in seen_images:
                continue
            seen_images.add(image.as_pointer())
            image_path = bpy.path.abspath(image.filepath) if image.filepath else ""
            if image_path and os.path.isfile(image_path):
                continue
            image_number += 1
            texture_name = bpy.path.clean_name(image.name) or "texture"
            temp_path = os.path.join(
                temp_dir,
                f"{base_name}_texture_{image_number:02d}_{texture_name}.png",
            )
            states.append((image, image.filepath_raw, image.file_format))
            try:
                image.filepath_raw = temp_path
                image.file_format = "PNG"
                image.save()
            except Exception as exc:
                print(f"Remi: could not save texture '{image.name}': {exc}")
                _restore_texture_export_images(states)
                for path in temp_files:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                return None
            temp_files.append(temp_path)
    return states, temp_files


def _obj_mtl_has_texture(filepath: str) -> bool:
    mtl_path = os.path.splitext(filepath)[0] + ".mtl"
    try:
        with open(mtl_path, encoding="utf-8", errors="replace") as mtl_file:
            return any(line.lstrip().startswith("map_") for line in mtl_file)
    except OSError:
        return False


def _restore_source_materials(source: bpy.types.Object, result: bpy.types.Object) -> None:
    source_materials = {
        material.name: material
        for material in source.data.materials
        if material
    }
    for index, material in enumerate(result.data.materials):
        source_material = source_materials.get(material.name) if material else None
        if source_material is None and index < len(source.data.materials):
            source_material = source.data.materials[index]
        if source_material:
            result.data.materials[index] = source_material


def _run_textured_worker(input_path: str, output_path: str, settings) -> list[dict]:
    worker = Path(__file__).resolve().parents[2] / "_decimate_worker.py"
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
        ],
        capture_output=True,
        text=True,
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
    if keep_texture and not _has_image_texture(obj):
        return None, "Keep Texture needs a mesh with UVs and an image texture", []

    with SessionDiskService.operation_workspace(disk, "decimate") as workspace:
        temp_dir = str(workspace)
        base_name = bpy.path.clean_name(obj.name)
        file_ext = "obj" if keep_texture else "ply"
        input_path = os.path.join(temp_dir, f"{base_name}_input.{file_ext}")
        output_path = os.path.join(temp_dir, f"{base_name}_decimated.{file_ext}")
        texture_image_states = []
        if keep_texture:
            texture_export = _prepare_texture_export_images(obj, temp_dir, base_name)
            if texture_export is None:
                return None, "Could not prepare the texture image for MeshLab", []
            texture_image_states, _texture_temp_files = texture_export
        try:
            export_ok = (
                mesh_exchange.export_obj_for_tool(
                    obj,
                    input_path,
                    export_materials=True,
                )
                if keep_texture
                else mesh_exchange.export_ply(obj, input_path)
            )
        finally:
            _restore_texture_export_images(texture_image_states)
        if not export_ok:
            kind = "Textured OBJ" if keep_texture else "PLY"
            return None, f"{kind} export failed", []
        if keep_texture and not _obj_mtl_has_texture(input_path):
            return None, "OBJ export did not include an image texture", []

        results = (
            _run_textured_worker(input_path, output_path, settings)
            if keep_texture
            else meshlab.run_multi_pass_decimation(
                input_path=input_path,
                output_path=output_path,
                passes=settings.decimation_passes,
                target_percentage=settings.target_percentage,
                preserve_detail=settings.decimation_preserve_detail,
            )
        )
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
            _restore_source_materials(obj, candidate)
        candidate.name = obj.name + settings.output_name_suffix
        candidate.pop("_remi_session_id", None)
        return candidate, "", results
