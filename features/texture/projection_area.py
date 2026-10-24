"""Screen-space lasso state and viewport overlay for view projection."""
from __future__ import annotations

from dataclasses import dataclass, field

import bpy
from mathutils import Vector

from .view_camera import viewport_region


@dataclass
class ProjectionAreaState:
    points: list[tuple[float, float]] = field(default_factory=list)
    drawing: bool = False
    region_pointer: int = 0
    area_pointer: int = 0
    region_x: int = 0
    region_y: int = 0
    width: int = 0
    height: int = 0
    view_matrix: tuple = ()
    lens: float = 50.0
    mesh_name: str = ""

    def begin(self, context, area, region, mesh):
        self.points = []
        self.drawing = False
        self.area_pointer = area.as_pointer()
        self.region_pointer = region.as_pointer()
        self.region_x = int(region.x)
        self.region_y = int(region.y)
        self.width = int(region.width)
        self.height = int(region.height)
        self.view_matrix = _matrix_tuple(region.data.view_matrix)
        self.lens = float(getattr(area.spaces.active, "lens", 50.0))
        self.mesh_name = mesh.name

    def clear(self):
        self.points = []
        self.drawing = False
        self.region_pointer = 0
        self.area_pointer = 0
        self.region_x = 0
        self.region_y = 0
        self.width = 0
        self.height = 0
        self.view_matrix = ()
        self.lens = 50.0
        self.mesh_name = ""

    def has_points(self):
        return len(self.points) >= 3

    def add_point(self, x, y):
        if not self.drawing:
            return
        if self.points:
            last_x, last_y = self.points[-1]
            if (x - last_x) ** 2 + (y - last_y) ** 2 < 4.0:
                return
        self.points.append((float(x), float(y)))

    def finish(self):
        self.drawing = False

    def matches_view(self, context):
        area, region = viewport_region(context)
        if area is None or region is None:
            return False
        if region.as_pointer() != self.region_pointer:
            return False
        if (int(region.x), int(region.y), int(region.width), int(region.height)) != (
            self.region_x,
            self.region_y,
            self.width,
            self.height,
        ):
            return False
        mesh = context.view_layer.objects.active
        if mesh is None or mesh.name != self.mesh_name:
            return False
        if abs(float(getattr(area.spaces.active, "lens", 50.0)) - self.lens) > 1e-5:
            return False
        current = _matrix_tuple(region.data.view_matrix)
        return _matrix_close(current, self.view_matrix)

    def valid(self, context):
        return self.has_points() and self.matches_view(context)

    def normalized(self):
        if self.width <= 0 or self.height <= 0:
            return []
        return [(x / self.width, y / self.height) for x, y in self.points]


def _matrix_tuple(matrix):
    return tuple(tuple(float(value) for value in row) for row in matrix)


def _matrix_close(first, second):
    if len(first) != len(second):
        return False
    return all(
        abs(float(a) - float(b)) <= 1e-6
        for row_a, row_b in zip(first, second)
        for a, b in zip(row_a, row_b)
    )


state = ProjectionAreaState()


def _draw_overlay():
    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if area is None or area.type != "VIEW_3D":
        return
    if region is None or region.type != "WINDOW":
        return
    if region.as_pointer() != state.region_pointer:
        return
    if not state.points or not state.matches_view(context):
        return
    if len(state.points) < 2 or region.width <= 0 or region.height <= 0:
        return

    # POST_PIXEL uses region pixel coordinates with the origin at the bottom
    # left. The lasso points use the same convention, so no matrix override is
    # needed; this also keeps the overlay aligned with the mouse.
    points = [(x, y, 0.0) for x, y in state.points]
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        from mathutils.geometry import tessellate_polygon

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")

        if len(points) >= 3:
            try:
                triangles = tessellate_polygon(
                    [[Vector((x, y, 0.0)) for x, y, _z in points]]
                )
                vertices = [
                    (vector.x, vector.y, 0.0)
                    for triangle in triangles
                    for vector in triangle
                ]
                if vertices:
                    batch = batch_for_shader(shader, "TRIS", {"pos": vertices})
                    shader.bind()
                    shader.uniform_float("color", (1.0, 0.45, 0.1, 0.20))
                    batch.draw(shader)
            except Exception:
                pass

        outline = points + [points[0]]
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": outline})
        shader.bind()
        shader.uniform_float("color", (1.0, 0.55, 0.1, 0.95))
        gpu.state.line_width_set(2.0)
        batch.draw(shader)
    except Exception:
        pass
    finally:
        try:
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.line_width_set(1.0)
        except Exception:
            pass


def register_draw_handler():
    return bpy.types.SpaceView3D.draw_handler_add(
        _draw_overlay, (), "WINDOW", "POST_PIXEL"
    )


def unregister_draw_handler(handle):
    if handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except (ValueError, ReferenceError):
            pass
