import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .guides import FieldStroke, project_detected_guides, reproject_guide, send_guides


class InteractiveRuntime:
    def __init__(self):
        self.session = None
        self.source_name = ""
        self.source_triangles = None
        self.surface_vertices = None
        self.surface_faces = None
        self.surface_normals = None
        self.surface_bvh = None
        self.strokes = []
        self.automatic_guides = []
        self.disabled_guide_keys = set()
        self.guide_notes = []
        self.preview_quality = {}
        self.preview_guide_quality = []
        self.guidance_pass = ""
        self.baseline_preview = None
        self.guidance_decision = ""
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
        self.preview_warnings = []
        self.pending_target_faces = None
        self.target_faces_deadline = 0.0
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
        self.source_triangles = None
        self.surface_vertices = None
        self.surface_faces = None
        self.surface_normals = None
        self.surface_bvh = None
        self.strokes = []
        self.automatic_guides = []
        self.disabled_guide_keys = set()
        self.guide_notes = []
        self.preview_quality = {}
        self.preview_guide_quality = []
        self.guidance_pass = ""
        self.baseline_preview = None
        self.guidance_decision = ""
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
        self.preview_warnings = []
        self.pending_target_faces = None
        self.target_faces_deadline = 0.0
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

    def start(self, obj, settings, *, target_faces=None, triangles=None, strokes=None,
              disabled_guide_keys=None):
        """Build the field session for the given object.

        target_faces overrides the scene setting so a density change can
        rebuild without writing back to the property that triggered it.
        triangles reuses an already-extracted source mesh, and strokes
        restores guide strokes across a rebuild.
        """
        self.shutdown()
        self.last_error = ""
        from ._native import GUIDE_API_VERSION, Session

        if GUIDE_API_VERSION < 1:
            raise RuntimeError("The bundled Instant Meshes solver is outdated; rebuild the native module or install a matching Remi package")

        if triangles is None:
            vertices, faces = self._evaluated_triangles(obj)
        else:
            vertices, faces = triangles
        if len(faces) == 0:
            raise RuntimeError("The evaluated object has no triangular surface")
        self.source_triangles = (vertices, faces)
        crease = float(np.degrees(settings.crease_angle)) if settings.preserve_creases else -1.0
        self.set_status("Building native hierarchy…", 0.0)
        self.session = Session(
            vertices,
            faces,
            target_faces=int(settings.target_faces if target_faces is None else target_faces),
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
        self.strokes = [reproject_guide(stroke, self.surface_bvh,
                                       max(self.average_edge_length * 0.5, 1e-12),
                                       max(self.average_edge_length * 0.1, 1e-10))
                        for stroke in strokes] if strokes else []
        self.disabled_guide_keys = set(disabled_guide_keys or ())
        if settings.auto_guides:
            self.detect_automatic_guides(solve=False)
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
        if self.strokes or self.automatic_guides:
            self._send_strokes()
        else:
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

    def _send_strokes(self, *, compare=True):
        settings = self.settings()
        automatic = self.automatic_guides if settings and settings.auto_guides else []
        if compare:
            self.baseline_preview = None
            self.guidance_decision = ""
            self.session.discard_saved_solution()
            self.guidance_pass = ("BASELINE" if settings and settings.auto_quality_guard
                                  and any(g.enabled for g in automatic) and not self.strokes else "")
        if self.guidance_pass:
            # Evaluate both passes from the same seed, not by perturbing the
            # preceding result. Only field arrays are saved, not a second hierarchy.
            send_guides(self.session, [])
            self.session.set_target_faces(self.session.target_faces)
        applied = [] if self.guidance_pass == "BASELINE" else automatic
        send_guides(self.session, applied + self.strokes,
                    strength=settings.auto_guide_strength if settings else 0.65)
        self.pending_position = True
        self.pending_preview = bool(self.guidance_pass or (settings and settings.auto_update_preview))
        self.position_ready = False
        self.solve_stage = "ORIENTATION"
        self.last_error = ""
        self.preview = None
        self.preview_normals = None
        self.preview_topology = None
        self.preview_quality = {}
        self.preview_warnings = []
        self.preview_guide_quality = []
        self.orientation = self.position = None
        self.orientation_singularities = self.position_singularities = None
        self.session.start_orientation()
        self.set_status("Applying guides · solving orientation (1/2)…", 0.0)
        ensure_timer()

    def detect_automatic_guides(self, *, solve=True, reset=False):
        """Replace suggestions, never the user's manually drawn guides."""
        if self.session is None or self.session.active:
            raise RuntimeError("Wait for the current field solve to finish")
        from .detection import detect_guides

        settings = self.settings()
        if reset:
            self.disabled_guide_keys.clear()
        if not settings.auto_guides:
            self.automatic_guides = []
            self.guide_notes = []
        else:
            self.set_status("Recognizing geometric guides…", 0.0)
            result = detect_guides(
                *self.source_triangles, circles=settings.auto_circles,
                cylinders=settings.auto_cylinders, curvature=settings.auto_curvature,
                crease_angle=float(np.degrees(settings.crease_angle)),
                tolerance=settings.auto_guide_tolerance, target_scale=self.target_scale,
                max_guides=settings.auto_guide_limit,
            )
            self.automatic_guides = project_detected_guides(
                result, self.surface_bvh, max(self.average_edge_length * 0.5, 1e-12),
                max(self.average_edge_length * 0.35, 1e-10),
            )
            for guide in self.automatic_guides:
                guide.enabled = guide.key not in self.disabled_guide_keys
            self.guide_notes = list(dict.fromkeys(result.notes))
        if solve:
            self._send_strokes()

    def toggle_automatic_guide(self, index):
        if not self.ready or self.session.active:
            raise RuntimeError("Wait for the current field solve to finish")
        guide = self.automatic_guides[index]
        guide.enabled = not guide.enabled
        if guide.enabled:
            self.disabled_guide_keys.discard(guide.key)
        else:
            self.disabled_guide_keys.add(guide.key)
        self._send_strokes()

    def clear_automatic_guides(self):
        if not self.ready or self.session.active:
            raise RuntimeError("Wait for the current field solve to finish")
        self.disabled_guide_keys.update(guide.key for guide in self.automatic_guides)
        self.automatic_guides = []
        self._send_strokes()

    def add_stroke(self, stroke_type, hits, *, closed=False):
        positions = np.asarray([tuple(hit[0]) for hit in hits], dtype=np.float32)
        normals = np.asarray([tuple(hit[1]) for hit in hits], dtype=np.float32)
        faces = np.asarray([hit[2] for hit in hits], dtype=np.int32)
        if closed and (len(positions) < 3 or np.linalg.norm(positions[-1] - positions[0]) >
                       max(self.target_scale * 0.75, self.average_edge_length * 4)):
            raise ValueError("Finish the closed guide near its starting point")
        stroke = FieldStroke(stroke_type, positions, normals, faces, closed=closed)
        if closed:
            stroke = reproject_guide(stroke, self.surface_bvh,
                                     max(self.average_edge_length * 0.5, 1e-12),
                                     max(self.average_edge_length * 0.1, 1e-10))
        self.strokes.append(stroke)
        try:
            self._send_strokes()
        except Exception:
            self.strokes.pop()
            raise

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
        self._send_strokes()

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
        # Extraction reports quality defects instead of refusing outright, so
        # the user can inspect the result and decide whether to keep it.
        self.preview_warnings = list(self.session.output_warnings)
        from .quality import guide_quality, mesh_quality, prefer_guided

        self.preview_quality = mesh_quality(*self.preview)
        if self.guidance_pass:
            self.preview_guide_quality = guide_quality(*self.preview, self.automatic_guides)
        if self.guidance_pass == "BASELINE":
            self.session.save_solution()
            self.baseline_preview = {
                name: getattr(self, name) for name in (
                    "preview", "preview_normals", "preview_topology", "preview_quality",
                    "preview_warnings", "preview_guide_quality", "orientation", "position",
                    "orientation_singularities", "position_singularities",
                )
            }
            self.guidance_pass = "GUIDED"
            self._send_strokes(compare=False)
            return
        if self.guidance_pass == "GUIDED" and self.baseline_preview is not None:
            baseline = self.baseline_preview
            selected, self.guidance_decision = prefer_guided(
                baseline["preview_quality"], self.preview_quality,
                baseline["preview_guide_quality"], self.preview_guide_quality,
            )
            if not selected:
                self.session.clear_strokes()
                self.session.restore_solution()
                for name, value in baseline.items():
                    setattr(self, name, value)
            else:
                self.session.discard_saved_solution()
            self.baseline_preview = None
            self.guidance_pass = ""
        topology = self.preview_topology
        if topology["boundary_edges"] == 0 and topology["nonmanifold_edges"] == 0:
            integrity = "watertight"
        else:
            integrity = (
                f'{topology["boundary_edges"]:,} boundary edge(s), '
                f'{topology["nonmanifold_edges"]:,} non-manifold edge(s)'
            )
        warning = (
            f' · ⚠ {self.preview_warnings[0]}'
            if self.preview_warnings
            else ""
        )
        self.set_status(
            f"Preview ready · {len(self.preview[1]):,} faces · "
            f'{topology["components"]:,} component(s) · {integrity}{warning}',
            1.0,
        )

    def _recover_baseline(self, error):
        baseline = self.baseline_preview
        if self.guidance_pass != "GUIDED" or baseline is None:
            return False
        self.session.stop()
        self.session.clear_strokes()
        self.session.restore_solution()
        for name, value in baseline.items():
            setattr(self, name, value)
        self.baseline_preview = None
        self.guidance_pass = self.solve_stage = ""
        self.pending_position = self.pending_preview = False
        self.position_ready = True
        self.guidance_decision = f"ordinary preview retained: guided candidate failed ({error})"
        self.set_status("Ordinary preview ready · guided candidate failed; source unchanged", 1.0)
        return True

    def request_target_faces(self, value):
        """Queue a density change, coalescing rapid edits into one re-solve.

        Dragging the face-count control fires an update per step. Reseeding and
        re-solving on every step would stall the viewport, so the work only
        starts once the value has been still for TARGET_FACES_DEBOUNCE seconds.
        """
        if not self.ready:
            return
        self.pending_target_faces = int(value)
        self.target_faces_deadline = time.monotonic() + TARGET_FACES_DEBOUNCE
        ensure_debounce_timer()

    def apply_target_faces(self, value):
        """Re-densify the quad preview at a new target without a restart."""
        if not self.ready:
            return
        if self.session.active:
            raise RuntimeError("Wait for the field solve to finish")
        value = int(value)
        if self.session.needs_input_subdivision(value):
            # The live session's input mesh is too coarse for this density and
            # a running session cannot refine its own input, so rebuild the
            # field session instead of re-seeding it.
            source = self.source
            settings = self.settings()
            strokes = list(self.strokes)
            triangles = self.source_triangles
            self.start(
                source,
                settings,
                target_faces=value,
                triangles=triangles,
                strokes=strokes,
                disabled_guide_keys=self.disabled_guide_keys.copy(),
            )
            self.pending_preview = True
            return
        self.session.set_target_faces(value)
        self.target_scale = float(self.session.scale)
        if self.settings().auto_guides:
            self.detect_automatic_guides(solve=False)
            self._send_strokes()
            self.pending_preview = True
            return
        # Reseeding discards both fields, so the staged solve starts over from
        # the orientation pass. Guide strokes live on the session and survive.
        self.orientation = None
        self.position = None
        self.orientation_singularities = None
        self.position_singularities = None
        self.preview = None
        self.preview_normals = None
        self.preview_topology = None
        self.preview_warnings = []
        self.position_ready = False
        self.pending_position = True
        self.pending_preview = True
        self.solve_stage = "ORIENTATION"
        self.session.start_orientation()
        self.set_status(f"Re-solving for {int(value):,} faces (1/2)…", 0.0)
        ensure_timer()

    def flush_target_faces(self):
        """Run a queued density change once it has settled. Returns True when done."""
        if self.pending_target_faces is None:
            return True
        if time.monotonic() < self.target_faces_deadline:
            return False
        if self.session is None or self.session.active:
            # Let the field solve finish; the value stays queued.
            return False
        value = self.pending_target_faces
        self.pending_target_faces = None
        try:
            self.apply_target_faces(value)
        except Exception as error:
            if self._recover_baseline(error):
                return True
            self.last_error = str(error)
            self.set_status(f"Instant Meshes error: {error}", 0.0)
        return True

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
                comparison = ("Ordinary candidate · " if self.guidance_pass == "BASELINE" else
                              "Guided candidate · " if self.guidance_pass == "GUIDED" else "")
                self.set_status(f"{comparison}Solving {stage} field ({step})…", self.session.progress)
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
                    if self.solve_stage:
                        return 0.1
                else:
                    self.set_status(
                        f"Fields ready · {len(self.strokes)} guide stroke(s) · update preview",
                        1.0,
                    )
            return None
        except Exception as error:
            if self._recover_baseline(error):
                return None
            self.last_error = str(error)
            self.solve_stage = ""
            self.pending_position = False
            self.pending_preview = False
            self.session.discard_saved_solution()
            self.baseline_preview = None
            self.guidance_pass = ""
            self.set_status(f"Instant Meshes error: {error}", 0.0)
            return None


runtime = InteractiveRuntime()


# Seconds of quiet after the last face-count edit before the preview re-solves.
TARGET_FACES_DEBOUNCE = 1.0
_DEBOUNCE_INTERVAL = 0.1


def _timer_callback():
    return runtime.poll()


def ensure_timer():
    if not bpy.app.timers.is_registered(_timer_callback):
        bpy.app.timers.register(_timer_callback, first_interval=0.1)


def _debounce_callback():
    return None if runtime.flush_target_faces() else _DEBOUNCE_INTERVAL


def ensure_debounce_timer():
    if not bpy.app.timers.is_registered(_debounce_callback):
        bpy.app.timers.register(_debounce_callback, first_interval=_DEBOUNCE_INTERVAL)
