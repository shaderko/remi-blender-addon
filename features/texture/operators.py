"""Modal Blender adapter for the external splat-to-texture projector."""
from __future__ import annotations

from pathlib import Path
import tempfile
import uuid

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from ...blender.splat_projection import (
    ensure_texture_uv,
    export_mesh_archive,
    projection_available,
    write_world_splat_archive,
)
from ...integrations.splat_project.client import ProjectionJob
from .service import (
    active_mesh,
    appearance_source,
    apply_preview,
    can_project,
    external_python,
    import_result,
    output_directory,
    source_is_valid,
)


class TextureRuntime:
    job = None
    timer = None
    manager = None
    temp_paths = ()
    cancel_requested = False

    def stop(self):
        try:
            if self.job is not None:
                self.job.cancel()
        finally:
            self.job = None
            self.cancel_requested = False
            if self.timer is not None and self.manager is not None:
                try:
                    self.manager.event_timer_remove(self.timer)
                except (ReferenceError, RuntimeError):
                    pass
            if self.manager is not None:
                try:
                    state = getattr(self.manager, "remi_texture", None)
                    if state is not None:
                        state.busy = False
                except (ReferenceError, AttributeError):
                    pass
            for path in self.temp_paths:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
            self.temp_paths = ()
            self.timer = self.manager = None


runtime = TextureRuntime()


def _phase_label(phase):
    text = str(phase or "")
    if text.startswith("Loading"):
        return "Loading mesh and Gaussians…"
    if text.startswith("Rasterizing"):
        return "Rasterizing texture…"
    if text.startswith("Saving"):
        return "Saving textures…"
    if text.startswith("Complete"):
        return "Finishing…"
    return "Projecting splats…"


class Remi_OT_ProjectSplats(Operator):
    bl_idname = "remi.project_splats"
    bl_label = "Project Splats"
    bl_description = "Bake Gaussian-splat appearance and normals onto the active mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return can_project(context)

    def invoke(self, context, event):
        settings = context.scene.remi_texture
        state = context.window_manager.remi_texture
        if context.window is None:
            self.report({"ERROR"}, "Texture projection needs an interactive Blender window")
            return {"CANCELLED"}

        mesh = active_mesh(context)
        if mesh is None:
            self.report({"ERROR"}, "Select one mesh in Object Mode")
            return {"CANCELLED"}

        try:
            if context.window_manager.remi_session.active:
                raise RuntimeError("Finish or cancel the Remi mesh session before projecting textures")
            generation = getattr(context.window_manager, "remi_generation", None)
            if generation is not None and (generation.busy or generation.mesh_busy):
                raise RuntimeError("Finish image generation or mesh conversion before projecting textures")
            python = external_python(context)
            if not python:
                raise RuntimeError("Choose the external Python in Generate settings first")
            source_object, archive, label = appearance_source(mesh, settings)
            if not source_is_valid(source_object, archive):
                raise RuntimeError("No splat source is linked to this mesh; choose a Splat Source")
            output = output_directory(context)
            output.mkdir(parents=True, exist_ok=True)

            state.status = "Preparing UVs…"
            state.status_kind = "PROGRESS"
            if settings.smooth_mesh:
                changed = False
                for polygon in mesh.data.polygons:
                    if not polygon.use_smooth:
                        polygon.use_smooth = True
                        changed = True
                if changed:
                    mesh.data.update()
                    context.view_layer.update()
            uv_name = ensure_texture_uv(mesh, int(settings.texture_size))
            if not mesh.data.uv_layers:
                raise RuntimeError("The mesh has no UV map")

            temporary = Path(bpy.app.tempdir or tempfile.gettempdir())
            mesh_archive = temporary / f"remi_texture_mesh_{uuid.uuid4().hex}.npz"
            runtime.temp_paths = (mesh_archive,)
            state.status = "Exporting mesh…"
            export_mesh_archive(context, mesh, mesh_archive)

            if source_object is not None:
                splat_archive = temporary / f"remi_texture_splats_{uuid.uuid4().hex}.npz"
                runtime.temp_paths = (mesh_archive, splat_archive)
                write_world_splat_archive(source_object, splat_archive)
            else:
                splat_archive = Path(archive)

            state.status = "Starting projector…"
            runtime.job = ProjectionJob.start(
                python=python,
                archive=splat_archive,
                mesh=mesh_archive,
                output=output,
                texture_size=int(settings.texture_size),
                samples=int(settings.samples),
                normal_source=settings.normal_source,
                normal_strength=float(settings.normal_strength),
                normal_smoothing=int(settings.normal_smoothing),
                fill_holes=bool(settings.fill_holes),
                fill_background=bool(settings.fill_background),
                margin_px=int(settings.margin_px),
                source=label,
            )
            runtime.manager = context.window_manager
            runtime.timer = runtime.manager.event_timer_add(0.25, window=context.window)
            runtime.cancel_requested = False
            state.busy = True
            state.status = "Projecting splats…"
            state.status_kind = "PROGRESS"
            state.mesh_name = mesh.name
            state.source_name = label
            state.directory = str(runtime.job.directory)
            self._mesh_name = mesh.name
            self._uv_name = uv_name
            self._scene = context.scene
            context.window_manager.modal_handler_add(self)
        except Exception as exc:
            runtime.stop()
            state.status = str(exc)
            state.status_kind = "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        state = context.window_manager.remi_texture
        if runtime.job is None:
            return {"CANCELLED"}
        if runtime.cancel_requested or (event.type == "ESC" and event.value == "PRESS"):
            runtime.stop()
            state.status = "Cancelled. Partial output is kept in the Texture folder."
            state.status_kind = "CANCELLED"
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        state.status = _phase_label(runtime.job.status().get("phase", ""))
        if context.area:
            context.area.tag_redraw()
        if runtime.job.poll() is None:
            return {"RUNNING_MODAL"}

        try:
            result = runtime.job.result()
            if context.scene != self._scene or context.mode != "OBJECT":
                raise RuntimeError("Textures saved, but the scene or mode changed. Project again when ready")
            mesh = bpy.data.objects.get(self._mesh_name)
            if mesh is None or mesh.type != "MESH":
                raise RuntimeError("The projected mesh is no longer available")
            import_result(
                mesh,
                result,
                source_name=state.source_name,
                pack_images=bool(context.scene.remi_texture.pack_images),
                normal_mode=context.scene.remi_texture.normal_map_mode,
                uv_name=self._uv_name,
            )
            state.directory = result["directory"]
            state.status = (
                f"Projected {result.get('report_data', {}).get('covered_texels', 0):,} texels"
            )
            state.status_kind = "SUCCESS"
            if context.area:
                context.area.tag_redraw()
            self.report({"INFO"}, state.status)
            return {"FINISHED"}
        except Exception as exc:
            state.status = str(exc)
            state.status_kind = "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            runtime.stop()

    def cancel(self, context):
        runtime.stop()


class Remi_OT_CancelProjection(Operator):
    bl_idname = "remi.cancel_projection"
    bl_label = "Cancel Projection"
    bl_description = "Stop texture projection without deleting partial output"

    @classmethod
    def poll(cls, context):
        return runtime.job is not None

    def execute(self, context):
        runtime.cancel_requested = True
        return {"FINISHED"}


class Remi_OT_TexturePreview(Operator):
    bl_idname = "remi.texture_preview"
    bl_label = "Preview Projection"
    bl_description = "Switch between the projected texture and its confidence mask"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        items=[("TEXTURE", "Texture", ""), ("CONFIDENCE", "Confidence", "")],
        default="TEXTURE",
    )

    @classmethod
    def poll(cls, context):
        mesh = active_mesh(context)
        return bool(mesh is not None and projection_available(mesh))

    def execute(self, context):
        mesh = active_mesh(context)
        if mesh is None:
            return {"CANCELLED"}
        try:
            apply_preview(mesh, self.mode)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}
