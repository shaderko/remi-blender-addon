"""Modal Blender adapter for the external splat-to-texture projector."""
from __future__ import annotations

from pathlib import Path
import tempfile
import uuid

import bpy
from bpy.props import EnumProperty, IntProperty
from bpy.types import Operator

from ...blender.gaussian_splats import import_splats, native_splats_available
from ...blender.splat_projection import (
    ensure_texture_uv,
    export_mesh_archive,
    projection_available,
    write_world_splat_archive,
)
from ...blender.texture_layers import (
    add_layer,
    delete_layer,
    rebuild_layer_material,
    sync_base_layer,
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
from .view_camera import render_splat_view, viewport_region


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


def _prepare_projection_mesh(context, mesh, settings, state):
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
    mesh["remi_texture_uv"] = uv_name
    return uv_name


def _remove_temporary_splat(obj):
    if obj is None:
        return
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        point_cloud = getattr(bpy.types, "PointCloud", None)
        if point_cloud is not None and isinstance(data, point_cloud):
            bpy.data.pointclouds.remove(data)
        elif isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)


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

            uv_name = _prepare_projection_mesh(context, mesh, settings, state)

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
            sync_base_layer(mesh)
            rebuild_layer_material(mesh)
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


class Remi_OT_ProjectView(Operator):
    bl_idname = "remi.project_view"
    bl_label = "Project from View"
    bl_description = "Render the linked splats from the current viewport camera and add the view as a projection layer"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(
            can_project(context)
            and native_splats_available()
            and viewport_region(context)[0] is not None
        )

    def invoke(self, context, event):
        settings = context.scene.remi_texture
        state = context.window_manager.remi_texture
        if context.window is None:
            self.report({"ERROR"}, "View projection needs an interactive Blender window")
            return {"CANCELLED"}
        if not native_splats_available():
            self.report({"ERROR"}, "View projection requires Blender 5.3+ native Gaussian splats")
            return {"CANCELLED"}
        area, region = viewport_region(context)
        if area is None or region is None:
            self.report({"ERROR"}, "Use Project from View from a 3D Viewport")
            return {"CANCELLED"}
        mesh = active_mesh(context)
        if mesh is None:
            self.report({"ERROR"}, "Select one mesh in Object Mode")
            return {"CANCELLED"}

        temp_splat = None
        previous_active = context.view_layer.objects.active
        previous_selected = list(context.selected_objects)
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
            uv_name = _prepare_projection_mesh(context, mesh, settings, state)

            temporary = Path(bpy.app.tempdir or tempfile.gettempdir())
            mesh_archive = temporary / f"remi_texture_mesh_{uuid.uuid4().hex}.npz"
            view_image = temporary / f"remi_texture_view_{uuid.uuid4().hex}.png"
            runtime.temp_paths = (mesh_archive, view_image)

            render_splat = source_object
            if render_splat is None:
                state.status = "Importing splat for view render…"
                state.status_kind = "PROGRESS"
                temp_splat = import_splats(context, archive, display="AUTO")
                render_splat = temp_splat
            if render_splat.name not in context.scene.objects:
                raise RuntimeError("The splat source is not in the active scene")
            state.status = "Rendering splat from current view…"
            state.status_kind = "PROGRESS"
            context.view_layer.update()
            with bpy.context.temp_override(area=area, region=region):
                view = render_splat_view(
                    context,
                    render_splat,
                    view_image,
                    base_resolution=int(settings.view_resolution),
                    mesh=mesh,
                )
            # The render call hides and restores render visibility; restore the
            # artist's selection before the modal worker starts.
            bpy.ops.object.select_all(action="DESELECT")
            for obj in previous_selected:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            context.view_layer.objects.active = mesh
            mesh.select_set(True)

            state.status = "Exporting mesh…"
            context.view_layer.update()
            export_mesh_archive(context, mesh, mesh_archive)

            state.status = "Starting view projector…"
            runtime.job = ProjectionJob.start_view(
                python=python,
                mesh=mesh_archive,
                view_image=view_image,
                view_matrix=view["view_matrix"],
                projection_matrix=view["projection_matrix"],
                output=output,
                texture_size=int(settings.texture_size),
                alpha_threshold=float(settings.view_alpha_threshold),
                source=label,
            )
            runtime.manager = context.window_manager
            runtime.timer = runtime.manager.event_timer_add(0.25, window=context.window)
            runtime.cancel_requested = False
            state.busy = True
            state.status = "Projecting view…"
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
            bpy.ops.object.select_all(action="DESELECT")
            for obj in previous_selected:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            context.view_layer.objects.active = previous_active
            state.status = str(exc)
            state.status_kind = "ERROR"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            # A successful path keeps the temp splat only until rendering is
            # done; it is never needed by the worker.
            if temp_splat is not None:
                _remove_temporary_splat(temp_splat)
                temp_splat = None
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
                raise RuntimeError("View layer saved, but the scene or mode changed. Import it when ready")
            mesh = bpy.data.objects.get(self._mesh_name)
            if mesh is None or mesh.type != "MESH":
                raise RuntimeError("The projected mesh is no longer available")
            sync_base_layer(mesh)
            layer = add_layer(
                mesh,
                name="View",
                image_path=result["layer"],
                kind="VIEW",
                alpha_mode="IMAGE",
                source=state.source_name,
                pack=bool(context.scene.remi_texture.pack_images),
            )
            mesh["remi_texture_view_report"] = str(result.get("report", ""))
            state.directory = result["directory"]
            visible = int(result.get("report_data", {}).get("visible_texels", 0))
            state.status = f"View layer added · {visible:,} texels"
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


class Remi_OT_DeleteTextureLayer(Operator):
    bl_idname = "remi.delete_texture_layer"
    bl_label = "Delete Texture Layer"
    bl_description = "Remove this projection layer from the mesh"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(min=0)

    @classmethod
    def poll(cls, context):
        mesh = active_mesh(context)
        return bool(mesh is not None and len(mesh.remi_texture_layers))

    def execute(self, context):
        mesh = active_mesh(context)
        if mesh is None:
            return {"CANCELLED"}
        if not delete_layer(mesh, self.index):
            self.report({"WARNING"}, "Layer no longer exists")
            return {"CANCELLED"}
        if context.area:
            context.area.tag_redraw()
        return {"FINISHED"}


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
