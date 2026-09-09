"""
Blender operators for the Remi pipeline.
"""

import os
import sys
import json
import math
import select
import subprocess
from pathlib import Path
import bmesh
import bpy
from bpy.types import Operator
from bpy.props import BoolProperty
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from . import gn_setup
from . import meshlab_wrapper as mlw
from . import autoremesher as arm
from . import baking
from . import alpha_wrap as aw
from .uv_mapping import ensure_remi_uv
from .workflow.disk_service import SessionDiskService
from .infrastructure.blender.mesh_exchange import (
    export_obj_for_tool as _export_obj_for_tool,
    export_ply as _export_ply,
    import_obj_result as _import_obj_result,
    import_ply as _import_ply,
)
from .infrastructure.blender.mesh_objects import (
    apply_modifiers as _apply_modifiers,
    duplicate_object as _duplicate_object,
    local_bounds_diagonal as _local_bounds_diagonal,
    world_bounds_diagonal as _world_bounds_diagonal,
)


from .features.repair.service import (
    _alpha_wrap_hole_patches,
    _closest_point_on_segment,
    _closing_volume_remesh,
    _commit_surface_ring_patch,
    _compose_source_with_guide_patches,
    _create_alpha_wrap_guide,
    _create_repair_candidate,
    _create_surface_ring_patch,
    _detail_recovery_distance,
    _dilate_guide_faces,
    _evaluated_world_sharp_edges,
    _evaluated_world_surface,
    _fit_volume_remesh_to_source,
    _guide_boundary_coverage,
    _guide_patch_faces,
    _guided_hole_patches,
    _hole_close_distance,
    _prepare_hole_repair,
    _repair_boundary_holes,
    _resample_screen_lasso,
    _resolve_alpha_wrap,
    _volume_hole_patches,
)















def _remove_mesh_object(obj: bpy.types.Object):
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)






























# ============================================================
# Operators
# ============================================================


class Remi_OT_DrawHolePatch(Operator):
    """Ray-project a viewport lasso and add a local repair patch."""

    bl_idname = "remi.draw_hole_patch"
    bl_label = "Draw Around Hole"
    bl_description = "Draw around one visible hole and commit a recoverable manual repair"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        state = getattr(context.window_manager, "remi_session", None)
        session_ready = bool(
            state is None
            or not state.active
            or (not state.busy and not state.interactive)
        )
        return bool(
            session_ready
            and context.mode == "OBJECT"
            and context.active_object
            and context.active_object.type == "MESH"
            and context.area
            and context.area.type == "VIEW_3D"
        )

    def _end_session_interaction(self, context, message=None):
        if not getattr(self, "_session_active", False):
            return
        from .session import runtime as session_runtime

        state = getattr(context.window_manager, "remi_session", None)
        if state is not None and state.active:
            state.interactive = False
            if message is not None and not state.busy:
                session_runtime._update_stats(context, message)

    def _viewport_point(self, event):
        x = event.mouse_x - self._window_region.x
        y = event.mouse_y - self._window_region.y
        if 0 <= x < self._window_region.width and 0 <= y < self._window_region.height:
            return (float(x), float(y))
        return None

    def _draw_overlay(self):
        if len(self._points) < 2:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader

        coordinates = list(self._points)
        if len(coordinates) > 2:
            coordinates.append(coordinates[0])
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": coordinates})
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(3.0)
        shader.bind()
        shader.uniform_float("color", (0.2, 0.8, 1.0, 0.95))
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")

    def _cleanup(self, context):
        if getattr(self, "_draw_handle", None) is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw_handle, "WINDOW")
            self._draw_handle = None
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass

    def invoke(self, context, event):
        self._window_region = next(
            (region for region in context.area.regions if region.type == "WINDOW"),
            None,
        )
        if self._window_region is None:
            self.report({"ERROR"}, "Could not find the 3D viewport region")
            return {"CANCELLED"}
        state = getattr(context.window_manager, "remi_session", None)
        self._session_active = bool(state is not None and state.active)
        if self._session_active:
            from .session import runtime as session_runtime

            if not session_runtime.ensure_active_object(context):
                self.report({"ERROR"}, "The locked Remi mesh is missing")
                return {"CANCELLED"}
            state.interactive = True
            state.status = "Draw on the intact surface around one hole; release to apply"
        self._region_3d = context.area.spaces.active.region_3d
        self._source_name = context.active_object.name
        self._points = []
        self._drawing = False
        self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_overlay,
            (),
            "WINDOW",
            "POST_PIXEL",
        )
        context.window.cursor_modal_set("CROSSHAIR")
        context.area.header_text_set(
            "Remi: draw ON the surrounding surface around one hole • release to build • Esc cancels"
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._cleanup(context)
            self._end_session_interaction(context, "Manual repair cancelled")
            return {"CANCELLED"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            point = self._viewport_point(event)
            if point is not None:
                self._points = [point]
                self._drawing = True
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._drawing:
            point = self._viewport_point(event)
            if point is not None:
                previous = self._points[-1]
                if (point[0] - previous[0]) ** 2 + (point[1] - previous[1]) ** 2 >= 9.0:
                    self._points.append(point)
                    context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self._drawing:
            self._drawing = False
            if len(self._points) < 3:
                self.report({"WARNING"}, "Draw a larger closed region around the hole")
                return {"RUNNING_MODAL"}
            source = bpy.data.objects.get(self._source_name)
            polygon = list(self._points)
            region = self._window_region
            region_3d = self._region_3d
            self._cleanup(context)
            if not source:
                self.report({"ERROR"}, "Source object was removed")
                self._end_session_interaction(context, "Manual repair failed: the mesh was removed")
                return {"CANCELLED"}

            from bpy_extras import view3d_utils

            source_bvh, _boundary_points = _evaluated_world_surface(source)
            if source_bvh is None:
                self.report({"ERROR"}, "The source has no usable surface geometry")
                self._end_session_interaction(context, "Manual repair failed: no usable surface")
                return {"CANCELLED"}
            settings = context.scene.remi_settings
            screen_samples = _resample_screen_lasso(
                polygon,
                float(settings.targeted_ray_spacing),
            )
            ray_hits = []
            for screen in screen_samples:
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, region_3d, screen)
                ray_direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, screen)
                hit = source_bvh.ray_cast(ray_origin, ray_direction)
                if hit[0] is not None:
                    ray_hits.append((hit[0], hit[1], float(hit[3])))
            minimum_hits = max(6, int(len(screen_samples) * 0.35))
            if len(ray_hits) < minimum_hits:
                self.report(
                    {"ERROR"},
                    "Too much of the stroke missed the mesh. Draw the loop on the visible surface around the hole.",
                )
                self._end_session_interaction(context, "Manual repair cancelled: redraw on the visible surface")
                return {"CANCELLED"}

            sorted_depths = sorted(depth for _point, _normal, depth in ray_hits)
            median_depth = sorted_depths[len(sorted_depths) // 2]
            depth_tolerance = (
                _world_bounds_diagonal(source) * float(settings.targeted_ray_depth_ratio)
            )
            ring_world = [
                point
                for point, _normal, depth in ray_hits
                if abs(depth - median_depth) <= depth_tolerance
            ]
            ring_normals = [
                normal
                for _point, normal, depth in ray_hits
                if abs(depth - median_depth) <= depth_tolerance
            ]
            if len(ring_world) < minimum_hits:
                self.report(
                    {"ERROR"},
                    "The stroke hit multiple depth layers. Lower Depth or redraw tightly on the front rim.",
                )
                self._end_session_interaction(context, "Manual repair cancelled: multiple depth layers")
                return {"CANCELLED"}

            result, error, report = _commit_surface_ring_patch(
                context,
                source,
                settings,
                ring_world,
                ring_normals=ring_normals,
            )
            if error:
                self.report({"ERROR"}, error)
                self._end_session_interaction(context)
                return {"CANCELLED"}
            was_session = self._session_active
            self._end_session_interaction(context)
            self.report(
                {"INFO"},
                (
                    f"Added {report.get('patch_faces', 0):,} patch faces to the Remi mesh"
                    if was_session
                    else f"Created '{result.name}' with {report.get('patch_faces', 0):,} patch faces"
                ),

            )
            return {"FINISHED"}

        return {"RUNNING_MODAL"}

    def cancel(self, context):
        self._cleanup(context)
        self._end_session_interaction(context, "Manual repair cancelled")




class Remi_OT_RepairHoles(Operator):
    """Prepare holes and cracks on a separate copy of the active mesh."""

    bl_idname = "remi.repair_holes"
    bl_label = "Repair Holes"
    bl_description = "Repair holes and cracks on a separate prepared copy"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and context.active_object.type == "MESH" and context.mode == "OBJECT")

    def execute(self, context):
        settings = context.scene.remi_settings
        source = context.active_object
        repaired, error, report = _create_repair_candidate(source, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        if settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
            faces = report.get("patch_faces", 0)
            coverage = report.get("boundary_coverage", 1.0)
            if report.get("guide_method") == "VOLUME":
                self.report(
                    {"INFO"},
                    f"Patched '{repaired.name}' with {faces:,} fitted volume-patch faces",
                )
            else:
                self.report(
                    {"INFO"},
                    f"Patched '{repaired.name}' with {faces:,} patch faces; "
                    f"{coverage:.0%} boundary coverage",
                )
            return {"FINISHED"}
        self.report(
            {"INFO"},
            f"Patched '{repaired.name}': {report['new_faces']} boundary patches, "
            f"close distance {report['close_distance']:.5g}",
        )
        return {"FINISHED"}


class Remi_OT_BuildAlphaWrap(Operator):
    """Configure and compile the bundled CGAL Alpha Wrap helper."""

    bl_idname = "remi.build_alpha_wrap"
    bl_label = "Build Alpha Wrap Helper"
    bl_description = "Compile the bundled C++ helper with CMake and the installed CGAL development package"

    def execute(self, context):
        result = aw.build_helper()
        if not result.get("success"):
            self.report({"ERROR"}, result.get("error", "Could not build Alpha Wrap helper"))
            return {"CANCELLED"}
        context.scene.remi_settings.alpha_wrap_executable = result["executable"]
        self.report({"INFO"}, "Alpha Wrap helper built successfully")
        return {"FINISHED"}

class Remi_OT_ImportGLB(Operator):
    """Import a GLB file into the scene."""
    bl_idname = "remi.import_glb"
    bl_label = "Import GLB"
    bl_description = "Import a GLB/glTF file into the scene"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")  # type: ignore

    def execute(self, context):
        settings = context.scene.remi_settings

        if self.filepath:
            filepath = self.filepath
        elif settings.import_glb_path:
            filepath = settings.import_glb_path
        else:
            self.report({"ERROR"}, "No GLB file specified")
            return {"CANCELLED"}

        if not os.path.exists(filepath):
            self.report({"ERROR"}, f"File not found: {filepath}")
            return {"CANCELLED"}

        # Import GLB/glTF
        prev_objects = set(bpy.context.scene.objects)
        try:
            bpy.ops.import_scene.gltf(filepath=filepath)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to import GLB: {e}")
            return {"CANCELLED"}

        # Find imported objects
        new_objs = [o for o in bpy.context.scene.objects if o not in prev_objects]
        if not new_objs:
            self.report({"WARNING"}, "No objects imported (file may be empty)")
            return {"CANCELLED"}

        # Select the first imported mesh
        for obj in new_objs:
            if obj.type == "MESH":
                bpy.context.view_layer.objects.active = obj
                obj.select_set(True)
                break

        self.report({"INFO"}, f"Imported {len(new_objs)} object(s) from GLB")
        return {"FINISHED"}

    def invoke(self, context, event):
        settings = context.scene.remi_settings
        if settings.import_glb_path:
            self.filepath = settings.import_glb_path
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


def _create_sdf_candidate(
    source,
    settings,
    suffix="_remesh",
    apply_result=False,
    candidate=None,
    disk=None,
):
    """Build an SDF result without changing ``source``."""
    if settings.remesh_backend == "VOLUME":
        result, error, report = _closing_volume_remesh(
            source,
            settings,
            suffix,
            result=candidate,
        )
        if not error:
            report["backend"] = "VOLUME"
        return result, error, report

    report = {"backend": "VOXEL"}
    if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
        result, error, patch_report = _guided_hole_patches(
            source,
            settings,
            suffix,
            prepared=candidate,
            disk=disk,
        )
        if error:
            return None, error, patch_report
        report.update(patch_report)
    else:
        result = candidate or _duplicate_object(source, suffix)
    try:
        result.select_set(True)
        bpy.context.view_layer.objects.active = result
        _prepare_hole_repair(result, settings)
        gn_setup.apply_remi_modifier(
            obj=result,
            voxel_size=settings.voxel_size,
            hole_close_distance=_hole_close_distance(result, settings),
            detail_recovery_distance=_detail_recovery_distance(result, settings),
            fillet_radius=settings.fillet_radius if settings.use_sdf_fillet else 0.0,
            smooth_iterations=settings.smoothing_iterations if settings.use_sdf_smoothing else 0,
        )
        if apply_result:
            _apply_modifiers(result)
    except Exception:
        _remove_mesh_object(result)
        raise
    return result, "", report


class Remi_OT_SDFRemesh(Operator):
    """Apply SDF voxel remesh to selected object (via geometry nodes on a copy)."""
    bl_idname = "remi.sdf_remesh"
    bl_label = "SDF Voxel Remesh"
    bl_description = "Duplicate selected object and apply SDF grid remesh via Geometry Nodes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings

        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        result, error, report = _create_sdf_candidate(obj, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        if settings.remesh_backend == "VOLUME":
            self.report(
                {"INFO"},
                f"Closing Volume created {report['faces']:,} faces; fitted "
                f"{report['surface_projected']:,} surface and {report['feature_fitted']:,} crease vertices",
            )
            return {"FINISHED"}

        if settings.use_hole_repair and settings.hole_repair_method in {"ALPHA_WRAP", "VOLUME"}:
            if report.get("guide_method") == "VOLUME":
                self.report({"INFO"}, f"Added {report['patch_faces']:,} fitted volume-patch faces")
            else:
                self.report(
                    {"INFO"},
                    f"Added {report['patch_faces']:,} hole-patch faces "
                    f"({report['boundary_coverage']:.0%} boundary coverage)",
                )
        self.report({"INFO"}, f"Applied SDF remesh to '{result.name}'")
        return {"FINISHED"}


class Remi_OT_ApplyRemesh(Operator):
    """Apply the SDF remesh modifier, converting it to real geometry."""
    bl_idname = "remi.apply_remesh"
    bl_label = "Apply Remesh"
    bl_description = "Apply the geometry nodes modifier to bake the remeshed geometry"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        # Find and apply AR modifier
        group = gn_setup.ensure_remi_node_group()
        found = False
        for mod in obj.modifiers:
            is_remi_group = (
                mod.type == "NODES"
                and mod.node_group
                and (
                    mod.node_group == group
                    or mod.node_group.name.startswith("Remi_SDF_Remesh")
                )
            )
            if is_remi_group:
                _apply_modifiers(obj)
                found = True
                break

        if not found:
            self.report({"ERROR"}, "No AR_SDF_Remesh modifier found on active object")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Applied remesh on '{obj.name}'")
        return {"FINISHED"}


from .features.remesh.decimation import create_candidate as _create_decimate_candidate


class Remi_OT_Decimate(Operator):
    """Run standalone PyMeshLab decimation on the active mesh."""
    bl_idname = "remi.decimate"
    bl_label = "Decimate (MeshLab)"
    bl_description = "Run MeshLab quadric edge collapse on the active object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}
        candidate, error, results = _create_decimate_candidate(obj, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        for result in results:
            print(
                f"Remi: Pass {result['pass']}: "
                f"{result.get('input_faces', '?')} → {result.get('output_faces', '?')} faces"
            )
        bpy.ops.object.select_all(action="DESELECT")
        candidate.select_set(True)
        context.view_layer.objects.active = candidate
        self.report({"INFO"}, f"Decimated model imported as '{candidate.name}'")
        return {"FINISHED"}


from .features.retopology.autoremesher_service import (
    create_candidate as _create_autoremesher_candidate,
)


class Remi_OT_AutoRemesher(Operator):
    """Run AutoRemesher external tool on the active mesh."""
    bl_idname = "remi.autoremesher"
    bl_label = "AutoRemesher (External)"
    bl_description = "Run the external AutoRemesher executable on the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.remi_settings
        obj = bpy.context.view_layer.objects.active
        if not obj or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object first")
            return {"CANCELLED"}

        candidate, error, _report = _create_autoremesher_candidate(obj, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        if settings.ar_hide_original:
            obj.hide_set(True)
        bpy.ops.object.select_all(action="DESELECT")
        candidate.select_set(True)
        context.view_layer.objects.active = candidate
        self.report({"INFO"}, f"AutoRemesher result imported as '{candidate.name}'")
        return {"FINISHED"}


from .features.bake.service import create_candidate as _create_bake_candidate
from .features.uv.service import create_candidate as _create_uv_candidate


class Remi_OT_GenerateUV(Operator):
    """Generate a validated Remi UV map for the active mesh."""

    bl_idname = "remi.generate_uv"
    bl_label = "Generate Remi UV"
    bl_description = "Analyze the active mesh, generate geometry-aware charts, unwrap, validate, and pack its UVs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(
            context.mode in {"OBJECT", "EDIT_MESH"}
            and context.active_object
            and context.active_object.type == "MESH"
        )

    def execute(self, context):
        obj = context.active_object
        settings = context.scene.remi_settings
        candidate, error, report = _create_uv_candidate(obj, settings)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        # Standalone UV keeps its historical in-place behavior while sharing
        # the same isolated candidate path as a Remi session.
        old_mesh = obj.data
        obj.data = candidate.data
        bpy.data.objects.remove(candidate, do_unlink=True)
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
        stats = report["stats"]
        if stats:
            self.report(
                {"INFO"},
                f"Remi UV: {report['chart_count']} charts, "
                f"p95 stretch {stats.conformal_p95:.2f}, "
                f"{stats.packing_occupancy:.0%} occupancy",
            )
        else:
            self.report({"INFO"}, "Remi UV map is ready")
        if report["warnings"]:
            print("Remi UV warnings: " + "; ".join(report["warnings"]))
        return {"FINISHED"}


class Remi_BakeOperatorMixin:
    """Bake albedo, roughness, normal, and AO maps onto the active mesh."""
    bake_passes = ("diffuse", "roughness", "normal", "ao")

    @classmethod
    def poll(cls, context):
        # Keep the buttons available; execute() gives a useful selection hint
        # when a source mesh has not been selected yet.
        return bool(
            context.mode == "OBJECT"
            and context.active_object
            and context.active_object.type == "MESH"
        )

    def execute(self, context):
        # The ACTIVE object receives the bake (= the remeshed/optimized mesh)
        # The other SELECTED objects provide the detail (= the original meshes)
        target = context.active_object
        sources = [o for o in context.selected_objects if o != target and o.type == "MESH"]
        if not sources:
            self.report({"ERROR"}, "Select original mesh(es) first, then Shift-select the target "
                                     "(optimized mesh) last so it becomes active")
            return {"CANCELLED"}

        s = context.scene.remi_settings
        result = baking.bake_textures(
            sources, target,
            texture_size=s.bake_texture_size,
            uv_method=s.bake_uv_method,
            uv_island_margin=s.bake_uv_island_margin,
            uv_profile=s.bake_uv_profile,
            uv_margin_px=s.bake_uv_margin_px,
            uv_preserve_seams=s.bake_uv_preserve_seams,
            auto_unwrap=s.bake_auto_unwrap,
            recalc_normals=s.bake_recalc_normals,
            cage_extrusion=s.bake_cage_extrusion,
            max_ray_distance=s.bake_max_ray_distance,
            passes=self.bake_passes,
        )
        if result["success"]:
            self.report({"INFO"}, f"Baked {', '.join(self.bake_passes)} → {target.name}")
        else:
            self.report({"ERROR"}, result.get("error", "Baking failed"))
            return {"CANCELLED"}
        return {"FINISHED"}


class Remi_OT_BakeAllMaps(Remi_BakeOperatorMixin, Operator):
    """Bake all available maps onto the active mesh."""

    bl_idname = "remi.bake_all_maps"
    bl_label = "Bake All Maps"
    bl_description = "Bake albedo, roughness, normal, and AO maps onto the active target mesh"
    bl_options = {"REGISTER", "UNDO"}


class Remi_OT_BakeDiffuse(Remi_BakeOperatorMixin, Operator):
    """Bake only the albedo/diffuse map onto the active mesh."""

    bl_idname = "remi.bake_diffuse"
    bl_label = "Bake Albedo"
    bl_description = "Bake only the diffuse/albedo map onto the active target mesh"
    bake_passes = ("diffuse",)


class Remi_OT_BakeRoughness(Remi_BakeOperatorMixin, Operator):
    """Bake only the roughness map onto the active mesh."""

    bl_idname = "remi.bake_roughness"
    bl_label = "Bake Roughness"
    bl_description = "Bake only the roughness map onto the active target mesh"
    bake_passes = ("roughness",)


class Remi_OT_BakeNormal(Remi_BakeOperatorMixin, Operator):
    """Bake only the tangent-space normal map onto the active mesh."""

    bl_idname = "remi.bake_normal"
    bl_label = "Bake Normal"
    bl_description = "Bake only the tangent-space normal map onto the active target mesh"
    bake_passes = ("normal",)


class Remi_OT_BakeAO(Remi_BakeOperatorMixin, Operator):
    """Bake only ambient occlusion onto the active mesh."""

    bl_idname = "remi.bake_ao"
    bl_label = "Bake Ambient Occlusion"
    bl_description = "Bake only ambient occlusion onto the active target mesh"
    bake_passes = ("ao",)


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
                gn_setup.apply_remi_modifier(
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
                gn_setup.apply_remi_modifier(
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
            worker = os.path.join(os.path.dirname(__file__), "_decimate_worker.py")
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


# ============================================================
# Registration
# ============================================================

classes = [
    Remi_OT_DrawHolePatch,
    Remi_OT_RepairHoles,
    Remi_OT_BuildAlphaWrap,
    Remi_OT_ImportGLB,
    Remi_OT_SDFRemesh,
    Remi_OT_ApplyRemesh,
    Remi_OT_Decimate,
    Remi_OT_AutoRemesher,
    Remi_OT_GenerateUV,
    Remi_OT_BakeAllMaps,
    Remi_OT_BakeDiffuse,
    Remi_OT_BakeRoughness,
    Remi_OT_BakeNormal,
    Remi_OT_BakeAO,
    Remi_OT_FullPipeline,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
