"""Create real Blender LOD objects from board sources, never export/bake jobs."""

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from .assets import OBJECT_ASSET_KEY, OBJECT_REVISION_KEY


def _scene_sources(context, asset):
    """Prefer live objects so their exact materials/images are shared by LODs."""
    objects = [obj for obj in context.view_layer.objects if obj.type == "MESH"
               and obj.get(OBJECT_ASSET_KEY) == asset.id
               and obj.get(OBJECT_REVISION_KEY, asset.artifact if not asset.revisions else "") == asset.artifact]
    if objects:
        return objects
    # Add Selected Object intentionally doesn't tag or modify original objects.
    # Its recorded source names still let LOD creation use the originals when
    # that card has not been replaced by a later edited revision.
    original = asset.settings.get("imported", {}) if not asset.revisions else {}
    if original.get("scene") == context.scene.name:
        names = original.get("objects", [])
        objects = [context.view_layer.objects.get(name) for name in names]
        if objects and all(obj is not None and obj.type == "MESH" for obj in objects):
            return objects
    if not Path(asset.mesh_source()).is_file():
        raise ValueError("The mesh is not in this scene and its source file is missing")
    from .editing import import_working_mesh
    objects = import_working_mesh(context, asset.mesh_source())
    for obj in objects:
        obj[OBJECT_ASSET_KEY] = asset.id
        obj[OBJECT_REVISION_KEY] = asset.artifact
    return objects


def create(context, identifiers):
    from . import canvas
    from .jobs import busy_elsewhere
    from ..remesh.lods import create_lods, _snapshot, _discard_new
    board = canvas.BOARD
    if board is None:
        raise ValueError("Open the Remi Board first")
    if context.mode != "OBJECT" or busy_elsewhere(context):
        raise ValueError("Finish the active Remi tool and switch to Object Mode first")
    wanted = set(identifiers)
    targets = [asset for asset in board.catalog.assets.values() if asset.id in wanted
               and asset.kind == "MESH" and asset.state == "READY" and not asset.hidden]
    if not targets:
        raise ValueError("Choose one or more completed mesh cards")
    layer = getattr(board, "view_layer", board.scene.view_layers[0])
    with context.temp_override(scene=board.scene, view_layer=layer, collection=board.scene.collection):
        current = bpy.context
        selected, active = list(current.selected_objects), layer.objects.active
        before = _snapshot()
        # Appending a card not already in Blender necessarily loads its source
        # images. Roll them back on failure, never duplicate them for each LOD.
        dependencies = {key: set(getattr(bpy.data, key)) for key in ("materials", "images", "node_groups")}
        try:
            results = []
            for asset in targets:
                sources = _scene_sources(current, asset)
                result = create_lods(sources, levels=board.scene.remi_settings.lod_levels,
                                     ratio=board.scene.remi_settings.lod_ratio)
                results.append(result)
            canvas.store_selection(current, board.selected)
            return results
        except Exception:
            _discard_new(before)
            created = [item for key, values in dependencies.items()
                       for item in set(getattr(bpy.data, key))-values]
            if created:
                bpy.data.batch_remove(ids=created)
            raise
        finally:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in selected:
                if obj.name in layer.objects:
                    obj.select_set(True)
            layer.objects.active = active


class REMI_BOARD_OT_create_lods(Operator):
    bl_idname = "remi_board.create_lods"
    bl_label = "Create LODs in Blender"
    bl_description = "Create scene LOD meshes sharing all original materials and texture maps; no baking or export"
    bl_options = {"REGISTER", "UNDO"}
    asset_id: StringProperty()

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, context):
        from ..remesh.lods import draw_settings
        draw_settings(self.layout, context.scene.remi_settings)

    def execute(self, context):
        from . import canvas
        try:
            results = create(context, [self.asset_id] if self.asset_id else canvas.BOARD.selected)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Created LODs in {len(results)} Blender collection(s); enable reduced meshes in the Outliner")
        return {"FINISHED"}


def register():
    bpy.utils.register_class(REMI_BOARD_OT_create_lods)


def unregister():
    bpy.utils.unregister_class(REMI_BOARD_OT_create_lods)
