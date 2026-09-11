"""Legacy multi-object full-pipeline operator.

The recoverable single-object session is the primary workflow. This operator ID
is retained so existing Blender files and scripts continue to register safely.
"""

import json
import os
from pathlib import Path
import select
import subprocess
import sys

import bpy
from bpy.types import Operator

from ..integrations import autoremesher as arm
from ..features.bake import engine as baking
from ..features.remesh import geometry_nodes
from ..integrations import meshlab as mlw
from ..features.repair.service import (
    _closing_volume_remesh,
    _detail_recovery_distance,
    _guided_hole_patches,
    _hole_close_distance,
    _prepare_hole_repair,
)
from ..blender.mesh_exchange import (
    export_obj_for_tool as _export_obj_for_tool,
    export_ply as _export_ply,
    import_obj_result as _import_obj_result,
    import_ply as _import_ply,
)
from ..blender.mesh_objects import (
    apply_modifiers as _apply_modifiers,
    duplicate_object as _duplicate_object,
    remove_mesh_object as _remove_mesh_object,
)
from ..workflow.disk import SessionDiskService


class Remi_OT_FullPipeline(Operator):
    """Run the full Remi pipeline — modal (non‑blocking) with progress."""
    bl_idname = "remi.full_pipeline"
    bl_label = "Remi Pipeline"
    bl_description = "SDF Remesh → Decimate → [AutoRemesher] → [Bake Textures]; Esc cancels between stages"
    bl_options = {"REGISTER"}

    # ── Modal state ──────────────────────────────────────────
    pipe_state: bpy.props.StringProperty(default="")
    pipe_step: bpy.props.IntProperty(default=0)
    pipe_total: bpy.props.IntProperty(default=1)
    pipe_next: bpy.props.StringProperty(default="")
    pipe_obj: bpy.props.StringProperty(default="")
    pipe_dup: bpy.props.StringProperty(default="")
    pipe_cur: bpy.props.StringProperty(default="")

    def status(self, context, msg):
        self.report({"INFO"}, msg)
        context.window_manager.progress_update(self.pipe_step)

    def fail(self, context, msg):
        self.report({"ERROR"}, msg)

    def cleanup(self, context, discard_generated=False):
        if hasattr(self, "_timer") and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.window_manager.progress_end()
        if hasattr(self, "_subproc") and self._subproc:
            try:
                self._subproc.kill()
                self._subproc.wait(timeout=1.0)
            except Exception:
                pass
            self._subproc = None
        disk = getattr(self, "_disk", None)
        if disk is not None:
            disk.close()
            self._disk = None
        self._temp_dir = ""
        if discard_generated:
            generated_names = {
                name
                for name in (getattr(self, "pipe_cur", ""), getattr(self, "pipe_dup", ""))
                if name and name != getattr(self, "pipe_obj", "")
            }
            for name in generated_names:
                generated = bpy.data.objects.get(name)
                if generated and generated.type == "MESH":
                    _remove_mesh_object(generated)
            source = bpy.data.objects.get(getattr(self, "pipe_obj", ""))
            if source:
                bpy.ops.object.select_all(action="DESELECT")
                source.select_set(True)
                context.view_layer.objects.active = source
        self.pipe_state = ""

    def go(self, context, state, msg=None):
        if msg:
            self.status(context, msg)
        self.pipe_state = state

    def start_subproc(self, cmd, next_state, context, status_msg):
        self._subproc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._subproc_log = []
        self.pipe_next = next_state
        self.go(context, "_SUB", status_msg)

    def _total(self, settings):
        n = 0
        n += 1 if settings.use_sdf_remesh else 0
        n += 1 if settings.use_decimation else 0
        n += 1 if settings.use_autoremesher else 0
        n += 1 if settings.use_baking else 0
        return n or 1

    # ── Synchronous fallback for background mode ───────────────
    def _sync_run(self, context, settings):
        """Run the whole pipeline synchronously (no modal)."""
        obj = context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object")
            return {"CANCELLED"}
        if settings.use_baking and not any((
            settings.use_sdf_remesh,
            settings.use_decimation,
            settings.use_autoremesher,
        )):
            self.report({"ERROR"}, "Enable a remesh or decimation stage before baking in the full pipeline")
            return {"CANCELLED"}
        if settings.use_decimation and not mlw.ensure_pymeshlab():
            self.report({"ERROR"}, mlw.pymeshlab_unavailable_message())
            return {"CANCELLED"}

        disk = SessionDiskService.create("full-pipeline")
        try:
            return self._sync_run_owned(context, settings, obj, disk)
        finally:
            disk.close()

    def _sync_run_owned(self, context, settings, obj, disk):
        temp_dir = str(disk.create_workspace("full-pipeline"))
        current = None
        dup = None

        if settings.use_sdf_remesh:
            if settings.remesh_backend == "VOLUME":
                self.report({"INFO"}, "Running fitted Closing Volume remesh...")
                dup, error, volume_report = _closing_volume_remesh(obj, settings, "_remesh")
                if error:
                    self.report({"ERROR"}, error)
                    return {"CANCELLED"}
                self.report(
                    {"INFO"},
                    f"Closing Volume fitted {volume_report.get('feature_fitted', 0):,} crease vertices",
                )
            else:
                if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
                    self.report({"INFO"}, "Preparing guide-derived hole patches...")
                    dup, error, patch_report = _guided_hole_patches(
                        obj,
                        settings,
                        "_remesh",
                        disk=disk,
                    )
                    if error:
                        self.report({"ERROR"}, error)
                        return {"CANCELLED"}
                    coverage = patch_report.get("boundary_coverage", 0.0)
                    if patch_report.get("guide_method") == "VOLUME":
                        self.report(
                            {"INFO"},
                            f"Volume preparation retained {patch_report.get('patch_faces', 0):,} fitted patch faces",
                        )
                    else:
                        report_type = "INFO" if coverage >= settings.alpha_wrap_coverage_target else "WARNING"
                        self.report({report_type}, f"Hole preparation: {coverage:.0%} boundary coverage")
                else:
                    self.report({"INFO"}, "SDF remeshing...")
                    dup = _duplicate_object(obj, "_remesh")
                    context.view_layer.objects.active = dup
                    dup.select_set(True)
                    _prepare_hole_repair(dup, settings)
                geometry_nodes.apply_remi_modifier(
                    obj=dup,
                    voxel_size=settings.voxel_size,
                    hole_close_distance=_hole_close_distance(dup, settings),
                    detail_recovery_distance=_detail_recovery_distance(dup, settings),
                    fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
                    smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
                )
                _apply_modifiers(dup)

        if settings.use_decimation:
            self.report({"INFO"}, "Decimating...")
            source = dup if dup else obj
            base = bpy.path.clean_name(source.name)
            inp = os.path.join(temp_dir, f"{base}_input.ply")
            out = os.path.join(temp_dir, f"{base}_decimated.ply")
            if not _export_ply(source, inp):
                self.report({"ERROR"}, "PLY export failed")
                return {"CANCELLED"}
            results = mlw.run_multi_pass_decimation(
                input_path=inp, output_path=out,
                passes=settings.decimation_passes,
                target_percentage=settings.target_percentage,
                preserve_detail=settings.decimation_preserve_detail)
            failed = next((result for result in results if not result["success"]), None)
            if failed:
                self.report(
                    {"ERROR"},
                    f"Decimation pass {failed['pass']} failed: {failed.get('error')}",
                )
                return {"CANCELLED"}
            current = _import_ply(out)
            if not current:
                self.report({"ERROR"}, "Failed to import decimated mesh")
                return {"CANCELLED"}
            if dup:
                current.name = dup.name
                bpy.data.objects.remove(dup, do_unlink=True)
                dup = None

        if settings.use_autoremesher:
            self.report({"INFO"}, "AutoRemesher...")
            source = current if current else (dup if dup else obj)
            exe = arm.resolve_executable(settings.autoremesher_executable)
            err = arm.validate_executable(exe)
            if err:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}
            base = bpy.path.clean_name(source.name)
            ar_in = os.path.join(temp_dir, f"{base}_ar_in.obj")
            ar_out = os.path.join(temp_dir, f"{base}_ar_out.obj")
            ar_rpt = os.path.join(temp_dir, f"{base}_ar_report.txt")
            if not _export_obj_for_tool(source, ar_in):
                return {"CANCELLED"}
            cmd = arm.build_command(
                exe, Path(ar_in), Path(ar_out), Path(ar_rpt),
                target_quads=settings.ar_target_quads,
                edge_scaling=settings.ar_edge_scaling,
                sharp_edge=settings.ar_sharp_edge,
                smooth_normal=settings.ar_smooth_normal,
                adaptivity=settings.ar_adaptivity,
            )
            proc = subprocess.run(cmd, cwd=str(exe.parent), capture_output=True, text=True)
            if proc.returncode != 0:
                self.report({"ERROR"}, proc.stderr.strip() or "AutoRemesher failed")
                return {"CANCELLED"}
            if not os.path.isfile(ar_out):
                self.report({"ERROR"}, "AutoRemesher produced no output")
                return {"CANCELLED"}
            if current:
                bpy.data.objects.remove(current, do_unlink=True)
            elif dup:
                bpy.data.objects.remove(dup, do_unlink=True)
                dup = None
            current = _import_obj_result(ar_out)
            if not current:
                self.report({"ERROR"}, "Failed to import AutoRemesher result")
                return {"CANCELLED"}
            current.name = base

        if settings.use_baking:
            self.report({"INFO"}, "Baking textures...")
            target = current if current else (dup if dup else obj)
            final_name = obj.name + settings.output_name_suffix
            result = baking.bake_textures(
                obj, target,
                texture_size=settings.bake_texture_size,
                final_name=final_name,
                uv_method=settings.bake_uv_method,
                uv_island_margin=settings.bake_uv_island_margin,
                uv_profile=settings.bake_uv_profile,
                uv_margin_px=settings.bake_uv_margin_px,
                uv_preserve_seams=settings.bake_uv_preserve_seams,
                auto_unwrap=settings.bake_auto_unwrap,
                recalc_normals=settings.bake_recalc_normals,
                cage_extrusion=settings.bake_cage_extrusion,
                max_ray_distance=settings.bake_max_ray_distance,
                auto_cage=settings.bake_auto_cage,
                )
            if not result["success"]:
                self.report({"ERROR"}, result.get("error", "Baking failed"))
                return {"CANCELLED"}
            target.name = final_name
        elif current:
            current.name = obj.name + settings.output_name_suffix
        elif dup:
            dup.name = obj.name + settings.output_name_suffix

        result_object = current or dup
        if result_object:
            bpy.ops.object.select_all(action="DESELECT")
            result_object.select_set(True)
            context.view_layer.objects.active = result_object
        self.report({"INFO"}, "Remi pipeline complete!")
        return {"FINISHED"}

    def execute(self, context):
        # In background mode, run synchronously (modal timers don't fire)
        if bpy.app.background or not context.window:
            return self._sync_run(context, context.scene.remi_settings)

        # In GUI mode, run modally for non-blocking progress
        settings = context.scene.remi_settings
        obj = context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object")
            return {"CANCELLED"}
        if settings.use_baking and not any((
            settings.use_sdf_remesh,
            settings.use_decimation,
            settings.use_autoremesher,
        )):
            self.report({"ERROR"}, "Enable a remesh or decimation stage before baking in the full pipeline")
            return {"CANCELLED"}
        if settings.use_decimation and not mlw.ensure_pymeshlab():
            self.report({"ERROR"}, mlw.pymeshlab_unavailable_message())
            return {"CANCELLED"}

        self.pipe_obj = obj.name
        self.pipe_step = 0
        self.pipe_total = self._total(settings)
        self.pipe_dup = ""
        # pipe_cur always identifies the latest process result.  Starting it
        # at the source also makes Decimation-only and AutoRemesher-only runs
        # valid; SDF replaces it with its duplicate below.
        self.pipe_cur = obj.name
        self.pipe_next = ""
        self._subproc = None
        self._disk = SessionDiskService.create("full-pipeline")
        try:
            self._temp_dir = str(self._disk.create_workspace("full-pipeline"))
            self._timer = context.window_manager.event_timer_add(
                0.15,
                window=context.window,
            )
        except Exception:
            self._disk.close()
            self._disk = None
            self._temp_dir = ""
            raise

        context.window_manager.modal_handler_add(self)
        context.window_manager.progress_begin(0, self.pipe_total)
        # Start at the first enabled step
        if settings.use_sdf_remesh:
            self.pipe_state = "SDF"
        elif settings.use_decimation:
            self.pipe_state = "EXPORT"
        elif settings.use_autoremesher:
            self.pipe_state = "AR_EXPORT"
        elif settings.use_baking:
            self.pipe_state = "BAKE"
        else:
            self.pipe_state = "DONE"
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            self.report({"INFO"}, "Remi pipeline cancelled; the source mesh was preserved")
            self.cleanup(context, discard_generated=True)
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        settings = context.scene.remi_settings

        # ── Subprocess polling ──────────────────────────────────
        sp = getattr(self, "_subproc", None)
        if sp is not None:
            # Read progress lines from stdout (non-blocking)
            sout = getattr(sp, "stdout", None)
            if sout is not None:
                r, _, _ = select.select([sout], [], [], 0)
                while r and sout:
                    line = sout.readline()
                    if not line:
                        break
                    try:
                        data = json.loads(line.strip())
                        if "pass" in data and "passes" in data:
                            p, tp = data["pass"], data["passes"]
                            self.status(context, f"Decimating... pass {p}/{tp}")
                    except json.JSONDecodeError:
                        message = line.strip()
                        if message:
                            self._subproc_log.append(message)
                            self._subproc_log = self._subproc_log[-40:]
                    r, _, _ = select.select([sout], [], [], 0)

            ret = sp.poll()
            if ret is None:
                return {"RUNNING_MODAL"}

            # Subprocess finished
            self._subproc = None
            if ret != 0:
                remainder = (sp.stdout.read() or "").strip() if sp.stdout else ""
                if remainder:
                    self._subproc_log.extend(remainder.splitlines())
                err = "\n".join(self._subproc_log[-20:]).strip()
                self.fail(context, err or "Subprocess failed")
                self.cleanup(context)
                return {"CANCELLED"}

            ns = self.pipe_next
            self.pipe_next = ""
            self.go(context, ns)
            return {"RUNNING_MODAL"}

        state = self.pipe_state
        if not state:
            return {"RUNNING_MODAL"}

        # ── SDF Remesh ──────────────────────────────────────────
        if state == "SDF":
            self.pipe_step += 1
            obj = bpy.data.objects.get(self.pipe_obj)
            if not obj:
                self.fail(context, "Source object lost")
                self.cleanup(context)
                return {"CANCELLED"}
            if settings.remesh_backend == "VOLUME":
                dup, error, volume_report = _closing_volume_remesh(obj, settings, "_remesh")
                if error:
                    self.fail(context, error)
                    self.cleanup(context)
                    return {"CANCELLED"}
                self.report(
                    {"INFO"},
                    f"Closing Volume fitted {volume_report.get('feature_fitted', 0):,} crease vertices",
                )
            else:
                if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
                    dup, error, patch_report = _guided_hole_patches(
                        obj,
                        settings,
                        "_remesh",
                        disk=self._disk,
                    )
                    if error:
                        self.fail(context, error)
                        self.cleanup(context)
                        return {"CANCELLED"}
                    coverage = patch_report.get("boundary_coverage", 0.0)
                    if patch_report.get("guide_method") == "VOLUME":
                        self.report(
                            {"INFO"},
                            f"Volume preparation retained {patch_report.get('patch_faces', 0):,} fitted patch faces",
                        )
                    else:
                        report_type = "INFO" if coverage >= settings.alpha_wrap_coverage_target else "WARNING"
                        self.report({report_type}, f"Hole preparation: {coverage:.0%} boundary coverage")
                else:
                    dup = _duplicate_object(obj, "_remesh")
                    context.view_layer.objects.active = dup
                    dup.select_set(True)
                    _prepare_hole_repair(dup, settings)
                geometry_nodes.apply_remi_modifier(
                    obj=dup,
                    voxel_size=settings.voxel_size,
                    hole_close_distance=_hole_close_distance(dup, settings),
                    detail_recovery_distance=_detail_recovery_distance(dup, settings),
                    fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
                    smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
                )
                _apply_modifiers(dup)
            self.pipe_dup = dup.name
            self.pipe_cur = dup.name
            stage_label = "SDF remesh"
            if settings.use_decimation:
                self.go(context, "EXPORT", f"{stage_label} done, exporting...")
            elif settings.use_autoremesher:
                self.pipe_step += 1  # skip decimation step
                self.go(context, "AR_EXPORT", "Exporting for AutoRemesher...")
            elif settings.use_baking:
                self.pipe_step += 2  # skip decimation + autoremesher
                self.go(context, "BAKE", "Baking textures...")
            else:
                self.go(context, "DONE", "Finalizing...")

        # ── Export PLY + start PyMeshLab subprocess ─────────────
        elif state == "EXPORT":
            current = bpy.data.objects.get(self.pipe_cur)
            if not current:
                self.fail(context, "Mesh lost before decimation")
                self.cleanup(context)
                return {"CANCELLED"}
            base = bpy.path.clean_name(current.name)
            inp = os.path.join(self._temp_dir, f"{base}_input.ply")
            out = os.path.join(self._temp_dir, f"{base}_decimated.ply")
            if not _export_ply(current, inp):
                self.fail(context, "PLY export failed")
                self.cleanup(context)
                return {"CANCELLED"}
            self.pipe_step += 1
            worker = os.path.join(str(Path(__file__).resolve().parents[1]), "_decimate_worker.py")
            self.start_subproc(
                [sys.executable, worker, inp, out,
                 str(settings.target_percentage), str(settings.decimation_passes),
                 str(settings.decimation_preserve_detail)],
                "IMPORT_DEC", context,
                f"Decimating... pass 1/{settings.decimation_passes}")

        # ── Import decimated result ─────────────────────────────
        elif state == "IMPORT_DEC":
            source = bpy.data.objects.get(self.pipe_cur)
            base = bpy.path.clean_name(source.name) if source else "remesh"
            out = os.path.join(self._temp_dir, f"{base}_decimated.ply")
            if not os.path.isfile(out):
                self.fail(context, "Decimated PLY not found")
                self.cleanup(context)
                return {"CANCELLED"}
            current = _import_ply(out)
            if not current:
                self.fail(context, "Failed to import decimated mesh")
                self.cleanup(context)
                return {"CANCELLED"}
            if source and source.name == self.pipe_dup:
                current.name = source.name
                bpy.data.objects.remove(source, do_unlink=True)
                self.pipe_dup = ""
            self.pipe_cur = current.name
            if settings.use_autoremesher:
                self.pipe_step += 1
                self.go(context, "AR_EXPORT", "Exporting for AutoRemesher...")
            elif settings.use_baking:
                self.pipe_step += 1
                self.go(context, "BAKE", "Baking textures...")
            else:
                self.go(context, "DONE", "Finalizing...")

        # ── AutoRemesher: export OBJ + start subprocess ─────────
        elif state == "AR_EXPORT":
            current = bpy.data.objects.get(self.pipe_cur)
            if not current:
                self.fail(context, "Mesh lost before AutoRemesher")
                self.cleanup(context)
                return {"CANCELLED"}
            exe = arm.resolve_executable(settings.autoremesher_executable)
            err = arm.validate_executable(exe)
            if err:
                self.fail(context, err)
                self.cleanup(context)
                return {"CANCELLED"}
            base = bpy.path.clean_name(current.name)
            ar_in = os.path.join(self._temp_dir, f"{base}_ar_in.obj")
            ar_out = os.path.join(self._temp_dir, f"{base}_ar_out.obj")
            ar_rpt = os.path.join(self._temp_dir, f"{base}_ar_report.txt")
            if not _export_obj_for_tool(current, ar_in):
                self.fail(context, "OBJ export failed")
                self.cleanup(context)
                return {"CANCELLED"}
            cmd = arm.build_command(
                exe, Path(ar_in), Path(ar_out), Path(ar_rpt),
                target_quads=settings.ar_target_quads,
                edge_scaling=settings.ar_edge_scaling,
                sharp_edge=settings.ar_sharp_edge,
                smooth_normal=settings.ar_smooth_normal,
                adaptivity=settings.ar_adaptivity,
            )
            self.start_subproc(cmd, "AR_IMPORT", context, "AutoRemesher running...")

        # ── Import AutoRemesher result ──────────────────────────
        elif state == "AR_IMPORT":
            current = bpy.data.objects.get(self.pipe_cur)
            base = bpy.path.clean_name(current.name) if current else "ar"
            ar_out = os.path.join(self._temp_dir, f"{base}_ar_out.obj")
            if not os.path.isfile(ar_out):
                self.fail(context, "AutoRemesher produced no output")
                self.cleanup(context)
                return {"CANCELLED"}
            rem = current.name if current else ""
            # Never delete the user's original in an AutoRemesher-only run.
            if current and current.name != self.pipe_obj:
                bpy.data.objects.remove(current, do_unlink=True)
            new_obj = _import_obj_result(ar_out)
            if not new_obj:
                self.fail(context, "Failed to import AutoRemesher result")
                self.cleanup(context)
                return {"CANCELLED"}
            new_obj.name = rem or "remesh_ar"
            self.pipe_cur = new_obj.name
            if settings.use_baking:
                self.pipe_step += 1
                self.go(context, "BAKE", "Baking textures...")
            else:
                self.go(context, "DONE", "Finalizing...")

        # ── Bake textures ───────────────────────────────────────
        elif state == "BAKE":
            src = bpy.data.objects.get(self.pipe_obj)
            cur = bpy.data.objects.get(self.pipe_cur)
            if not src or not cur:
                self.fail(context, "Objects missing for baking")
                self.cleanup(context)
                return {"CANCELLED"}
            if src == cur:
                self.fail(context, "Baking needs a generated target; enable a remesh or decimation stage")
                self.cleanup(context)
                return {"CANCELLED"}
            final_name = src.name + settings.output_name_suffix
            result = baking.bake_textures(
                src, cur,
                texture_size=settings.bake_texture_size,
                final_name=final_name,
                uv_method=settings.bake_uv_method,
                uv_island_margin=settings.bake_uv_island_margin,
                uv_profile=settings.bake_uv_profile,
                uv_margin_px=settings.bake_uv_margin_px,
                uv_preserve_seams=settings.bake_uv_preserve_seams,
                auto_unwrap=settings.bake_auto_unwrap,
                recalc_normals=settings.bake_recalc_normals,
                cage_extrusion=settings.bake_cage_extrusion,
                max_ray_distance=settings.bake_max_ray_distance,
                auto_cage=settings.bake_auto_cage,
            )
            if result["success"]:
                self.status(context, f"Baked: {', '.join(result['images'])}")
            else:
                self.fail(context, result.get("error", "Baking failed"))
                self.cleanup(context)
                return {"CANCELLED"}
            self.go(context, "DONE", "Finalizing...")

        # ── Finalize ────────────────────────────────────────────
        elif state == "DONE":
            src = bpy.data.objects.get(self.pipe_obj)
            cur = bpy.data.objects.get(self.pipe_cur)
            if cur and src:
                cur.name = src.name + settings.output_name_suffix
                bpy.ops.object.select_all(action="DESELECT")
                cur.select_set(True)
                context.view_layer.objects.active = cur
            self.pipe_step = self.pipe_total
            context.window_manager.progress_update(self.pipe_step)
            self.report({"INFO"}, "Remi pipeline complete!")
            self.cleanup(context)
            return {"FINISHED"}

        return {"RUNNING_MODAL"}
