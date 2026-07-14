import bpy
import numpy as np
from bpy.props import EnumProperty
from bpy.types import Operator

from .runtime import runtime


class REMI_OT_instant_meshes_start(Operator):
    bl_idname = "remi.instant_meshes_start"
    bl_label = "Start Interactive Retopology"
    bl_description = "Build a native Instant Meshes field session for the active evaluated mesh"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(context.mode == "OBJECT" and context.active_object and context.active_object.type == "MESH")

    def execute(self, context):
        try:
            runtime.start(context.active_object, context.scene.remi_instant_meshes)
        except Exception as error:
            runtime.shutdown()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_OT_instant_meshes_draw(Operator):
    bl_idname = "remi.instant_meshes_draw"
    bl_label = "Draw Field Guide"
    bl_description = "Draw directly on the surface to guide the Instant Meshes field"
    bl_options = {"REGISTER", "UNDO"}

    stroke_type: EnumProperty(
        items=[
            ("ORIENTATION", "Orientation Comb", "Guide nearby quad directions"),
            ("EDGE", "Output Edge Guide", "Guide directions and place an output edge along the stroke"),
        ],
        default="ORIENTATION",
    )

    @classmethod
    def poll(cls, context):
        return bool(runtime.ready and context.area and context.area.type == "VIEW_3D")

    def _viewport_point(self, event):
        x = event.mouse_x - self._region.x
        y = event.mouse_y - self._region.y
        if 0 <= x < self._region.width and 0 <= y < self._region.height:
            return float(x), float(y)
        return None

    def _draw_pending(self):
        if len(self._screen_points) < 2:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": self._screen_points})
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(3.0)
        shader.bind()
        color = (1.0, 0.55, 0.1, 1.0) if self.stroke_type == "EDGE" else (0.1, 0.9, 1.0, 1.0)
        shader.uniform_float("color", color)
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
        if runtime.session.active:
            self.report({"WARNING"}, "Wait for the current field solve to finish")
            return {"CANCELLED"}
        self._region = next((region for region in context.area.regions if region.type == "WINDOW"), None)
        if self._region is None:
            return {"CANCELLED"}
        self._region_3d = context.area.spaces.active.region_3d
        self._screen_points = []
        self._hits = []
        self._drawing = False
        self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_pending, (), "WINDOW", "POST_PIXEL"
        )
        context.window.cursor_modal_set("CROSSHAIR")
        tool = "output edge" if self.stroke_type == "EDGE" else "orientation"
        context.area.header_text_set(
            f"Remi Instant Meshes: draw a {tool} guide on the surface · release to solve · Esc cancels"
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._cleanup(context)
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            point = self._viewport_point(event)
            if point is not None:
                hit = runtime.ray_hit(self._region, self._region_3d, point)
                if hit:
                    self._screen_points = [point]
                    self._hits = [hit]
                    self._drawing = True
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE" and self._drawing:
            point = self._viewport_point(event)
            if point is not None:
                previous = self._screen_points[-1]
                if (point[0] - previous[0]) ** 2 + (point[1] - previous[1]) ** 2 >= 16.0:
                    hit = runtime.ray_hit(self._region, self._region_3d, point)
                    if hit:
                        self._screen_points.append(point)
                        self._hits.append(hit)
                        context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self._drawing:
            self._drawing = False
            if len(self._hits) < 2:
                self.report({"WARNING"}, "Draw a longer stroke on the visible surface")
                return {"RUNNING_MODAL"}
            try:
                runtime.add_stroke(1 if self.stroke_type == "EDGE" else 0, self._hits)
            except Exception as error:
                self.report({"ERROR"}, str(error))
                self._cleanup(context)
                return {"CANCELLED"}
            self._cleanup(context)
            return {"FINISHED"}
        return {"RUNNING_MODAL"}


class REMI_OT_instant_meshes_undo_stroke(Operator):
    bl_idname = "remi.instant_meshes_undo_stroke"
    bl_label = "Undo Last Guide"

    @classmethod
    def poll(cls, context):
        return runtime.ready and bool(runtime.strokes)

    def execute(self, context):
        runtime.undo_stroke()
        return {"FINISHED"}


class REMI_OT_instant_meshes_clear_strokes(Operator):
    bl_idname = "remi.instant_meshes_clear_strokes"
    bl_label = "Clear Guides"

    @classmethod
    def poll(cls, context):
        return runtime.ready and bool(runtime.strokes)

    def execute(self, context):
        runtime.clear_strokes()
        return {"FINISHED"}


class REMI_OT_instant_meshes_solve_orientation(Operator):
    bl_idname = "remi.instant_meshes_solve_orientation"
    bl_label = "Rebuild Fields"

    @classmethod
    def poll(cls, context):
        return runtime.ready and not runtime.session.active

    def execute(self, context):
        runtime.solve_orientation()
        return {"FINISHED"}


class REMI_OT_instant_meshes_solve_position(Operator):
    bl_idname = "remi.instant_meshes_solve_position"
    bl_label = "Solve Position"

    @classmethod
    def poll(cls, context):
        return runtime.ready and not runtime.session.active

    def execute(self, context):
        runtime.solve_position()
        return {"FINISHED"}


class REMI_OT_instant_meshes_preview(Operator):
    bl_idname = "remi.instant_meshes_preview"
    bl_label = "Update Quad Preview"

    @classmethod
    def poll(cls, context):
        return runtime.ready and runtime.position_ready and not runtime.session.active

    def execute(self, context):
        try:
            runtime.update_preview()
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_OT_instant_meshes_accept(Operator):
    bl_idname = "remi.instant_meshes_accept"
    bl_label = "Accept Retopology"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return runtime.ready and runtime.preview is not None and not runtime.session.active

    def execute(self, context):
        if not runtime.ready:
            return {"CANCELLED"}
        try:
            if runtime.preview is None:
                runtime.update_preview()
            vertices, native_faces = runtime.preview
            faces = []
            for face in native_faces:
                values = [int(value) for value in face]
                if len(values) == 4 and values[2] == values[3]:
                    values = values[:3]
                faces.append(values)
            source = runtime.source
            name = source.name + context.scene.remi_instant_meshes.output_suffix
            mesh = bpy.data.meshes.new(name + "_mesh")
            mesh.from_pydata(vertices.tolist(), [], faces)
            mesh.update()
            result = bpy.data.objects.new(name, mesh)
            collection = source.users_collection[0] if source.users_collection else context.collection
            collection.objects.link(result)
            result.matrix_world = source.matrix_world.copy()
            if context.scene.remi_instant_meshes.hide_source:
                source.hide_set(True)
            bpy.ops.object.select_all(action="DESELECT")
            result.select_set(True)
            context.view_layer.objects.active = result
            runtime.shutdown()
            context.scene.remi_instant_meshes.status = f"Created {result.name}"
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_OT_instant_meshes_cancel(Operator):
    bl_idname = "remi.instant_meshes_cancel"
    bl_label = "Cancel Session"

    @classmethod
    def poll(cls, context):
        return runtime.ready

    def execute(self, context):
        runtime.shutdown()
        context.scene.remi_instant_meshes.status = "Session cancelled"
        return {"FINISHED"}


classes = (
    REMI_OT_instant_meshes_start,
    REMI_OT_instant_meshes_draw,
    REMI_OT_instant_meshes_undo_stroke,
    REMI_OT_instant_meshes_clear_strokes,
    REMI_OT_instant_meshes_solve_orientation,
    REMI_OT_instant_meshes_solve_position,
    REMI_OT_instant_meshes_preview,
    REMI_OT_instant_meshes_accept,
    REMI_OT_instant_meshes_cancel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    runtime.shutdown()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
