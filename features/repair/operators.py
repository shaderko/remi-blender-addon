"""Blender operator adapters for repair actions."""

import bpy
from bpy.types import Operator

from ... import alpha_wrap as aw
from ...infrastructure.blender.mesh_objects import (
    world_bounds_diagonal as _world_bounds_diagonal,
)
from .service import (
    _commit_surface_ring_patch,
    _create_repair_candidate,
    _evaluated_world_surface,
    _resample_screen_lasso,
)


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
        from ...workflow.session_runtime import runtime as session_runtime

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
            from ...workflow.session_runtime import runtime as session_runtime

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
