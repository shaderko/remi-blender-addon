"""Edit a board mesh in its own ordinary Blender scene; apply to the same card."""

from copy import deepcopy
import json
from pathlib import Path
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator
from mathutils import Vector

from . import canvas, workspace
from .assets import Catalog, OBJECT_ASSET_KEY, SCENE_ASSETS_KEY
from .jobs import busy_elsewhere


EDIT_ASSET = "remi_board_edit_asset"
EDIT_OWNER = "remi_board_edit_owner"
EDIT_BASE = "remi_board_edit_base"
EDIT_WORKSPACE = "remi_board_mesh_editor"
_request = None


def source_scene(scene):
    owner = scene.get(EDIT_OWNER)
    return owner if isinstance(owner, bpy.types.Scene) else scene


def editing_scene(context):
    scene = context.scene
    return scene if scene.get(EDIT_ASSET) and source_scene(scene) != scene else None


def catalog_for(context):
    owner = source_scene(context.scene)
    board = canvas.BOARD
    if board is not None and board.scene == owner:
        return board.catalog
    with context.temp_override(scene=owner):
        saved = json.loads(owner.get(SCENE_ASSETS_KEY, "{}"))
        if saved.get("gallery") != canvas.layout_key(context):
            raise ValueError("The source board is not available")
    return Catalog(saved.get("assets", []))


def appearance_source(catalog, asset):
    explicit = asset.links.get("texture_source")
    candidates = [catalog.assets.get(explicit)] if explicit else catalog.ancestors(asset.id)
    return next((item for item in candidates if item is not None and item.kind == "SPLAT"
                 and item.state == "READY" and Path(item.artifact).is_file()), None)


def _discard_import(before):
    from ...blender.session_objects import SessionObjectStore
    for item in set(bpy.data.objects)-before["objects"]:
        bpy.data.objects.remove(item, do_unlink=True)
    SessionObjectStore.remove_new_orphans(before)


def import_working_mesh(context, path):
    """Import independent data; multiple mesh parts are normal Blender objects."""
    from ...blender.session_objects import SessionObjectStore
    before = SessionObjectStore.dependency_snapshot()
    try:
        path = Path(path)
        if path.suffix.lower() == ".ply":
            from ...blender.reconstructed_mesh import import_reconstructed_mesh
            import_reconstructed_mesh(context, path)
        elif path.suffix.lower() in {".glb", ".gltf"}:
            bpy.ops.import_scene.gltf(filepath=str(path))
        else:
            raise ValueError("Unsupported board mesh file")
        meshes = [obj for obj in context.scene.objects if obj not in before["objects"] and obj.type == "MESH"]
        if not meshes:
            raise ValueError("The asset contains no mesh")
        for obj in meshes:
            obj.pop(OBJECT_ASSET_KEY, None)
        context.view_layer.objects.active = meshes[0]
        return meshes
    except Exception:
        _discard_import(before)
        raise


def open_edit_scene(context, identifier):
    if context.window is None:
        raise ValueError("Editing needs a Blender window")
    if context.mode != "OBJECT" or busy_elsewhere(context):
        raise ValueError("Finish the active tool and switch to Object Mode first")
    owner = source_scene(context.scene)
    catalog = catalog_for(context)
    asset = catalog.assets.get(identifier)
    if asset is None or asset.kind != "MESH" or asset.state != "READY" or asset.hidden:
        raise ValueError("Choose a completed mesh card")
    scene = next((item for item in bpy.data.scenes
                  if item.get(EDIT_OWNER) == owner and item.get(EDIT_ASSET) == identifier), None)
    if scene is not None:
        context.window.scene = scene
        return scene
    if not Path(asset.artifact).is_file():
        raise ValueError("The source mesh file is missing")
    from ...blender.session_objects import SessionObjectStore
    before = SessionObjectStore.dependency_snapshot()
    scene = bpy.data.scenes.new("Edit · " + (asset.title or "Mesh"))
    scene.use_fake_user = True
    scene[EDIT_OWNER], scene[EDIT_ASSET], scene[EDIT_BASE] = owner, identifier, asset.artifact
    previous = context.window.scene
    try:
        context.window.scene = scene
        with context.temp_override(scene=scene, view_layer=scene.view_layers[0], collection=scene.collection):
            meshes = import_working_mesh(bpy.context, asset.artifact)
            source = appearance_source(catalog, asset)
            for obj in meshes:
                if source is not None:
                    obj["remi_mesh_source_archive"] = source.artifact
            # Native Remi tools remain optional, with the user's interpreter and
            # settings available. No session, flow, or modal controller starts.
            for name in ("remi_settings", "remi_generation", "remi_texture"):
                original, target = getattr(owner, name), getattr(scene, name)
                for prop in original.bl_rna.properties:
                    if not prop.is_readonly and prop.type not in {"POINTER", "COLLECTION"}:
                        setattr(target, prop.identifier, getattr(original, prop.identifier))
        return scene
    except Exception:
        context.window.scene = previous
        _discard_import(before)
        bpy.data.scenes.remove(scene)
        raise


def can_apply(context):
    return bool(editing_scene(context) is not None
                and context.mode in {"OBJECT", "EDIT_MESH", "SCULPT", "PAINT_TEXTURE", "PAINT_VERTEX", "PAINT_WEIGHT"}
                and not busy_elsewhere(context)
                and any(obj.type == "MESH" for obj in context.view_layer.objects))


def _export_glb(artifact):
    # Include every mesh part, not only the active/selected object. Modifiers
    # are evaluated in the GLB; their editable stacks remain in the edit scene.
    return bpy.ops.export_scene.gltf(filepath=str(artifact), export_format="GLB",
                                      use_selection=True, use_active_scene=True, export_apply=True)


def apply_to_card(context):
    if not can_apply(context):
        raise ValueError("Finish the active tool before applying this mesh")
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    scene = context.scene
    owner = source_scene(scene)
    catalog = catalog_for(context)
    asset = catalog.assets.get(scene[EDIT_ASSET])
    if asset is None or asset.hidden or asset.state != "READY":
        raise ValueError("The mesh card is no longer available")
    if asset.artifact != scene[EDIT_BASE]:
        raise ValueError("This card changed since the edit was opened; its newer file has been kept")
    with context.temp_override(scene=owner):
        directory = Path(canvas.layout_key(context)) / ".remi-board" / ("edit_" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    artifact = directory / "asset.glb"
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    meshes = [obj for obj in context.view_layer.objects if obj.type == "MESH"]
    visibility = [(obj, obj.hide_get(), obj.hide_viewport, obj.hide_select) for obj in meshes]
    try:
        for obj in selected:
            obj.select_set(False)
        for obj, _hidden, _viewport, _locked in visibility:
            obj.hide_viewport = obj.hide_select = False
            obj.hide_set(False)
            obj.select_set(True)
        context.view_layer.objects.active = meshes[0]
        result = _export_glb(artifact)
        if result != {"FINISHED"} or not artifact.is_file() or artifact.stat().st_size < 20:
            raise RuntimeError("Could not export the mesh; your edit is still open")
    except Exception:
        artifact.unlink(missing_ok=True)
        directory.rmdir()
        raise
    finally:
        for obj, hidden, viewport, locked in visibility:
            obj.select_set(False)
            obj.hide_set(hidden)
            obj.hide_viewport, obj.hide_select = viewport, locked
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
    previous = deepcopy(asset.document())
    asset.revisions.append({key: deepcopy(previous[key]) for key in
                            ("artifact", "directory", "preview", "preview_view", "settings")})
    asset.artifact, asset.directory, asset.preview, asset.preview_view = str(artifact), str(directory), "", []
    asset.status = "Ready"
    asset.settings["blender_edit"] = {"scene": scene.name, "objects": [obj.name for obj in meshes]}
    board = canvas.BOARD
    mounted = board is not None and board.scene == owner
    try:
        with context.temp_override(scene=owner, view_layer=owner.view_layers[0]):
            if mounted:
                canvas.save_layout(bpy.context)
            else:
                payload = canvas.load_layout(bpy.context)
                payload["assets"] = catalog.documents()
                canvas.write_layout(bpy.context, payload)
            canvas.store_selection(bpy.context, {asset.id})
    except Exception:
        for key, value in previous.items():
            setattr(asset, key, value)
        artifact.unlink(missing_ok=True)
        directory.rmdir()
        raise
    if mounted:
        if board.runner.preview_id == asset.id and board.runner.preview_job is not None:
            board.runner.preview_job.cancel()
            board.runner.preview_job, board.runner.preview_id = None, ""
        board.invalidate_preview(asset.id)
        board.release_texture(asset.id)
        board.selected = {asset.id}
    scene[EDIT_BASE] = asset.artifact
    return asset


def _schedule(context, phase, owner, identifier=""):
    global _request
    _request = (context.window.as_pointer(), phase, owner.name, identifier)
    if not bpy.app.timers.is_registered(_resume):
        bpy.app.timers.register(_resume, first_interval=0.1)


def _resume():
    global _request
    request, _request = _request, None
    if request is None:
        return None
    window_id, phase, owner_name, identifier = request
    owner = bpy.data.scenes.get(owner_name)
    window = next((item for item in bpy.context.window_manager.windows if item.as_pointer() == window_id), None)
    if window is None or owner is None:
        return None
    try:
        with bpy.context.temp_override(window=window):
            context = bpy.context
            if phase == "BOARD":
                window.scene = owner
                workspace.open_workspace(context)
            elif phase == "OPEN":
                editor = next((item for item in bpy.data.workspaces if item.get(EDIT_WORKSPACE)), None)
                if editor is not None:
                    window.workspace = editor
                    _request = (window_id, "SHOW", owner_name, identifier)
                else:
                    base = bpy.data.workspaces.get("Layout") or next((item for item in bpy.data.workspaces
                        if not item.get(workspace.WORKSPACE_KEY)), None)
                    if base is None:
                        raise RuntimeError("A normal Blender workspace is needed for mesh editing")
                    window.workspace = base
                    _request = (window_id, "CREATE", owner_name, identifier)
            elif phase == "CREATE":
                before = set(bpy.data.workspaces)
                bpy.ops.workspace.duplicate()
                editor = next(item for item in bpy.data.workspaces if item not in before)
                editor.name = "Mesh Editor"
                editor[EDIT_WORKSPACE] = True
                editor.pop(workspace.WORKSPACE_KEY, None)
                window.workspace = editor
                _request = (window_id, "SHOW", owner_name, identifier)
            elif phase == "SHOW":
                window.scene = owner
                open_edit_scene(context, identifier)
                _request = (window_id, "FRAME", owner_name, identifier)
            else:
                for area in window.screen.areas:
                    if area.type == "VIEW_3D":
                        with context.temp_override(area=area, region=canvas.window_region(area)):
                            context.view_layer.update()
                            points = [obj.matrix_world @ Vector(corner) for obj in context.scene.objects
                                      if obj.type == "MESH" for corner in obj.bound_box]
                            if points:
                                lower = Vector([min(point[axis] for point in points) for axis in range(3)])
                                upper = Vector([max(point[axis] for point in points) for axis in range(3)])
                                view = area.spaces.active.region_3d
                                view.view_location = (lower + upper) * 0.5
                                view.view_distance = max((upper-lower).length * 1.75, 0.1)
                                view.view_perspective = "PERSP"
                            bpy.ops.ed.undo_push(message="Open mesh editing scene")
                        break
    except Exception as exc:
        _request = None
        message = str(exc)
        bpy.context.window_manager.popup_menu(lambda menu, _context: menu.layout.label(text=message),
                                              title="Mesh editing", icon="ERROR")
    return 0.1 if _request is not None else None


class REMI_BOARD_OT_edit_mesh(Operator):
    bl_idname = "remi_board.edit_mesh"
    bl_label = "Edit in Blender"
    bl_description = "Open this mesh in its own Blender scene, without starting a Remi flow"
    asset_id: StringProperty()

    def execute(self, context):
        try:
            asset = catalog_for(context).assets.get(self.asset_id)
            if asset is None or asset.kind != "MESH" or asset.state != "READY" or asset.hidden:
                raise ValueError("Choose a completed mesh card")
            if context.mode != "OBJECT" or busy_elsewhere(context):
                raise ValueError("Finish the active tool and switch to Object Mode first")
            owner = source_scene(context.scene)
            if context.screen.show_fullscreen:
                with context.temp_override(area=context.screen.areas[0]):
                    bpy.ops.screen.screen_full_area()
            _schedule(context, "OPEN", owner, self.asset_id)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_apply_edit(Operator):
    bl_idname = "remi_board.apply_edit"
    bl_label = "Apply to Card"
    bl_description = "Update this same mesh card; keep earlier files and leave your main scene untouched"
    return_to_board: BoolProperty(name="Return to Board", default=True)

    @classmethod
    def poll(cls, context):
        return can_apply(context)

    def execute(self, context):
        try:
            asset = apply_to_card(context)
            if self.return_to_board:
                bpy.ops.remi_board.return_to_board()
            self.report({"INFO"}, f"Updated card: {asset.title or 'Mesh'}")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class REMI_BOARD_OT_return_to_board(Operator):
    bl_idname = "remi_board.return_to_board"
    bl_label = "Back to Board"
    bl_description = "Return to your original scene and board, keeping this edit for later"

    def execute(self, context):
        if busy_elsewhere(context):
            self.report({"WARNING"}, "Finish the active Remi tool first")
            return {"CANCELLED"}
        owner = source_scene(context.scene)
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if context.screen.show_fullscreen:
            with context.temp_override(area=context.screen.areas[0]):
                bpy.ops.screen.screen_full_area()
        _schedule(context, "BOARD", owner)
        return {"FINISHED"}


def draw_edit_header(self, context):
    if editing_scene(context) is None:
        self.layout.operator("remi_board.open", text="Remi Board", icon="WORKSPACE")
        return
    row = self.layout.row(align=True)
    row.label(text="Mesh card", icon="SCENE_DATA")
    apply = row.row(align=True)
    apply.enabled = can_apply(context)
    apply.operator("remi_board.apply_edit", icon="CHECKMARK")
    row.operator("remi_board.return_to_board", icon="BACK")


@persistent
def before_load(_unused):
    global _request
    _request = None


CLASSES = (REMI_BOARD_OT_edit_mesh, REMI_BOARD_OT_apply_edit, REMI_BOARD_OT_return_to_board)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_pre.append(before_load)
    bpy.types.VIEW3D_HT_header.append(draw_edit_header)


def unregister():
    before_load(None)
    if bpy.app.timers.is_registered(_resume):
        bpy.app.timers.unregister(_resume)
    if before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(before_load)
    bpy.types.VIEW3D_HT_header.remove(draw_edit_header)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
