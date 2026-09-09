from dataclasses import dataclass
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


@dataclass
class FieldStroke:
    stroke_type: int
    positions: np.ndarray
    normals: np.ndarray
    faces: np.ndarray


class InteractiveRuntime:
    def __init__(self):
        self.session = None
        self.source_name = ""
        self.surface_vertices = None
        self.surface_faces = None
        self.surface_normals = None
        self.surface_bvh = None
        self.strokes = []
        self.orientation = None
        self.position = None
        self.orientation_singularities = None
        self.position_singularities = None
        self.preview = None
        self.preview_normals = None
        self.source_topology = None
        self.preview_topology = None
        self.average_edge_length = 0.0
        self.target_scale = 0.0
        self.solve_stage = ""
        self.pending_position = False
        self.pending_preview = False
        self.position_ready = False
        self.last_visual_refresh = 0.0
        self.last_error = ""

    @property
    def ready(self):
        return self.session is not None and bpy.data.objects.get(self.source_name) is not None

    @property
    def source(self):
        return bpy.data.objects.get(self.source_name)

    def settings(self):
        scene = getattr(bpy.context, "scene", None)
        return getattr(scene, "remi_instant_meshes", None) if scene else None

    def set_status(self, message, progress=None):
        settings = self.settings()
        if settings:
            settings.status = message
            if progress is not None:
                settings.progress = max(0.0, min(1.0, float(progress)))
            settings.session_active = self.ready
        self.redraw()

    def redraw(self):
        wm = getattr(bpy.context, "window_manager", None)
        if not wm:
            return
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()

    def shutdown(self):
        session = self.session
        self.session = None
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass
            del session
        self.source_name = ""
        self.surface_vertices = None
        self.surface_faces = None
        self.surface_normals = None
        self.surface_bvh = None
        self.strokes = []
        self.orientation = None
        self.position = None
        self.orientation_singularities = None
        self.position_singularities = None
        self.preview = None
        self.preview_normals = None
        self.source_topology = None
        self.preview_topology = None
        self.average_edge_length = 0.0
        self.target_scale = 0.0
        self.solve_stage = ""
        self.pending_position = False
        self.pending_preview = False
        self.position_ready = False
        self.last_visual_refresh = 0.0
        self.last_error = ""
        settings = self.settings()
        if settings:
            settings.session_active = False
            settings.progress = 0.0
        self.redraw()

    def _evaluated_triangles(self, obj):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            vertices = np.empty((len(mesh.vertices), 3), dtype=np.float32)
            mesh.vertices.foreach_get("co", vertices.ravel())
            faces = np.empty((len(mesh.loop_triangles), 3), dtype=np.int32)
            mesh.loop_triangles.foreach_get("vertices", faces.ravel())
            return vertices, faces
        finally:
            evaluated.to_mesh_clear()

    def start(self, obj, settings):
        self.shutdown()
        self.last_error = ""
        from ._native import Session

        vertices, faces = self._evaluated_triangles(obj)
        if len(faces) == 0:
            raise RuntimeError("The evaluated object has no triangular surface")
        crease = float(np.degrees(settings.crease_angle)) if settings.preserve_creases else -1.0
        self.set_status("Building native hierarchy…", 0.0)
        self.session = Session(
            vertices,
            faces,
            target_faces=settings.target_faces,
            pure_quad=settings.pure_quad,
            crease_angle=crease,
            extrinsic=settings.extrinsic,
            align_boundaries=settings.align_boundaries,
            deterministic=settings.deterministic,
            smooth_iterations=settings.smooth_iterations,
        )
        self.source_name = obj.name
        self.average_edge_length = float(self.session.average_edge_length)
        self.target_scale = float(self.session.scale)
        self.source_topology = dict(self.session.source_topology)
        surface = self.session.surface_snapshot()
        self.surface_vertices = np.asarray(surface[0], dtype=np.float32)
        self.surface_faces = np.asarray(surface[1], dtype=np.int32)
        self.surface_normals = np.asarray(surface[2], dtype=np.float32)
        self.surface_bvh = BVHTree.FromPolygons(
            [Vector(value) for value in self.surface_vertices],
            [tuple(int(index) for index in face) for face in self.surface_faces],
            all_triangles=True,
        )
        self.strokes = []
        self.orientation = None
        self.position = None
        self.preview = None
        self.preview_normals = None
        self.preview_topology = None
        self.solve_stage = "ORIENTATION"
        self.pending_position = True
        self.pending_preview = bool(settings.auto_update_preview)
        self.position_ready = False
        settings.session_active = True
        self.session.start_orientation()
        self.set_status("Solving orientation field (1/2)…", 0.0)
        ensure_timer()

    def ray_hit(self, region, region_3d, coordinate):
        if not self.ready or self.surface_bvh is None:
            return None
        from bpy_extras import view3d_utils

        origin = view3d_utils.region_2d_to_origin_3d(region, region_3d, coordinate)
        direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, coordinate)
        inverse = self.source.matrix_world.inverted()
        local_origin = inverse @ origin
        local_direction = (inverse.to_3x3() @ direction).normalized()
        hit = self.surface_bvh.ray_cast(local_origin, local_direction)
        if hit[0] is None:
            return None
        return hit[0].copy(), hit[1].copy(), int(hit[2])

    def _send_strokes(self):
        if not self.strokes:
            self.session.clear_strokes()
        else:
            types = np.asarray([stroke.stroke_type for stroke in self.strokes], dtype=np.int32)
            offsets = [0]
            for stroke in self.strokes:
                offsets.append(offsets[-1] + len(stroke.positions))
            positions = np.concatenate([stroke.positions for stroke in self.strokes]).astype(np.float32)
            normals = np.concatenate([stroke.normals for stroke in self.strokes]).astype(np.float32)
            faces = np.concatenate([stroke.faces for stroke in self.strokes]).astype(np.int32)
            self.session.set_strokes(
                types,
                np.asarray(offsets, dtype=np.int32),
                positions,
                normals,
                faces,
            )
        settings = self.settings()
        self.pending_position = True
        self.pending_preview = bool(settings and settings.auto_update_preview)
        self.position_ready = False
        self.solve_stage = "ORIENTATION"
        self.preview = None
        self.preview_normals = None
        self.preview_topology = None
        self.session.start_orientation()
        self.set_status("Applying guides · solving orientation (1/2)…", 0.0)
        ensure_timer()

    def add_stroke(self, stroke_type, hits):
        positions = np.asarray([tuple(hit[0]) for hit in hits], dtype=np.float32)
        normals = np.asarray([tuple(hit[1]) for hit in hits], dtype=np.float32)
        faces = np.asarray([hit[2] for hit in hits], dtype=np.int32)
        self.strokes.append(FieldStroke(stroke_type, positions, normals, faces))
        self._send_strokes()

    def undo_stroke(self):
        if self.strokes:
            self.strokes.pop()
            self._send_strokes()

    def clear_strokes(self):
        self.strokes.clear()
        self._send_strokes()

    def solve_orientation(self):
        if not self.ready:
            return
        settings = self.settings()
        self.pending_position = True
        self.pending_preview = bool(settings and settings.auto_update_preview)
        self.position_ready = False
        self.preview = None
        self.preview_normals = None
        self.preview_topology = None
        self.solve_stage = "ORIENTATION"
        self.session.start_orientation()
        self.set_status("Rebuilding orientation field (1/2)…", 0.0)
        ensure_timer()

    def solve_position(self):
        if not self.ready:
            return
        settings = self.settings()
        self.pending_position = False
        self.pending_preview = bool(settings and settings.auto_update_preview)
        self.position_ready = False
        self.preview = None
        self.preview_normals = None
        self.preview_topology = None
        self.solve_stage = "POSITION"
        self.session.start_position()
        self.set_status("Solving position field (2/2)…", 0.0)
        ensure_timer()

    def refresh_orientation(self, include_singularities=True):
        settings = self.settings()
        samples = settings.field_samples if settings else 4000
        self.orientation = tuple(
            np.asarray(value, dtype=np.float32)
            for value in self.session.orientation_snapshot(samples)
        )
        if not include_singularities:
            return
        try:
            values = self.session.orientation_singularities()
            self.orientation_singularities = (
                np.asarray(values[0], dtype=np.float32),
                np.asarray(values[1], dtype=np.int32),
            )
        except Exception:
            self.orientation_singularities = None

    def refresh_position(self, include_singularities=True):
        settings = self.settings()
        samples = settings.field_samples if settings else 4000
        self.position = tuple(
            np.asarray(value, dtype=np.float32)
            for value in self.session.position_snapshot(samples)
        )
        if not include_singularities:
            return
        try:
            values = self.session.position_singularities()
            self.position_singularities = (
                np.asarray(values[0], dtype=np.float32),
                np.asarray(values[1], dtype=np.int32),
            )
        except Exception:
            self.position_singularities = None

    def update_preview(self):
        if not self.ready or self.session.active:
            raise RuntimeError("Wait for the field solve to finish")
        if not self.session.position_solved:
            raise RuntimeError("The position field must be solved before preview extraction")
        self.set_status("Extracting quad preview…", 1.0)
        self.preview = None
        self.preview_topology = None
        self.preview_normals = None
        vertices, faces, normals = self.session.extract()
        self.preview = (
            np.asarray(vertices, dtype=np.float32),
            np.asarray(faces, dtype=np.int32),
        )
        self.preview_normals = np.asarray(normals, dtype=np.float32)
        self.preview_topology = dict(self.session.output_topology)
        topology = self.preview_topology
        if topology["boundary_edges"] == 0 and topology["nonmanifold_edges"] == 0:
            integrity = "watertight"
        else:
            integrity = (
                f'{topology["boundary_edges"]:,} boundary edge(s), '
                f'{topology["nonmanifold_edges"]:,} non-manifold edge(s)'
            )
        self.set_status(
            f"Preview ready · {len(self.preview[1]):,} faces · "
            f'{topology["components"]:,} component(s) · {integrity}',
            1.0,
        )

    def poll(self):
        if not self.ready:
            return None
        try:
            if self.session.active:
                stage = "orientation" if self.solve_stage == "ORIENTATION" else "position"
                now = time.monotonic()
                settings = self.settings()
                if now - self.last_visual_refresh >= 0.25:
                    if self.solve_stage == "ORIENTATION" and settings and settings.show_orientation:
                        self.refresh_orientation(include_singularities=False)
                    elif self.solve_stage == "POSITION" and settings and settings.show_position:
                        self.refresh_position(include_singularities=False)
                    self.last_visual_refresh = now
                step = "1/2" if self.solve_stage == "ORIENTATION" else "2/2"
                self.set_status(f"Solving {stage} field ({step})…", self.session.progress)
                return 0.1

            if self.solve_stage == "ORIENTATION":
                self.refresh_orientation()
                if self.pending_position:
                    self.pending_position = False
                    self.solve_stage = "POSITION"
                    self.session.start_position()
                    self.set_status("Solving position field (2/2)…", 0.0)
                    return 0.1
                self.solve_stage = ""
                self.set_status(
                    f"Orientation ready · {len(self.strokes)} guide stroke(s)", 1.0
                )
            elif self.solve_stage == "POSITION":
                self.refresh_position()
                self.position_ready = bool(self.session.position_solved)
                self.solve_stage = ""
                if self.pending_preview:
                    self.pending_preview = False
                    self.update_preview()
                else:
                    self.set_status(
                        f"Fields ready · {len(self.strokes)} guide stroke(s) · update preview",
                        1.0,
                    )
            return None
        except Exception as error:
            self.last_error = str(error)
            self.solve_stage = ""
            self.pending_position = False
            self.pending_preview = False
            self.set_status(f"Instant Meshes error: {error}", 0.0)
            return None


runtime = InteractiveRuntime()


def _timer_callback():
    return runtime.poll()


def ensure_timer():
    if not bpy.app.timers.is_registered(_timer_callback):
        bpy.app.timers.register(_timer_callback, first_interval=0.1)
