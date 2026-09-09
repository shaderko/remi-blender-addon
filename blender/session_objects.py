"""Blender data-block mechanics used by the workflow transaction engine."""

from __future__ import annotations

import json

import bpy

from ..storage.disk import CHECKPOINT_MATERIALS_KEY, SESSION_ID_KEY


class SessionObjectStore:
    """Load, copy, replace, and clean session-owned Blender mesh objects."""

    @staticmethod
    def find(session_id: str):
        if not session_id:
            return None
        return next(
            (obj for obj in bpy.data.objects if obj.get(SESSION_ID_KEY) == session_id),
            None,
        )

    @staticmethod
    def remove(obj):
        if not obj:
            return
        mesh = obj.data if obj.type == "MESH" else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)

    @staticmethod
    def exists(obj) -> bool:
        try:
            return bool(obj and bpy.data.objects.get(obj.name) == obj)
        except ReferenceError:
            return False

    @staticmethod
    def select_only(context, obj):
        if not obj:
            return
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
        bpy.ops.object.select_all(action="DESELECT")
        obj.hide_set(False)
        obj.hide_select = False
        obj.select_set(True)
        context.view_layer.objects.active = obj

    def create_working_copy(self, context, source, label: str, *, linked=True):
        """Create an independent mesh while leaving the locked source untouched."""
        working = source.copy()
        working.data = source.data.copy()
        working.name = f"{source.name}_Remi{label.replace(' ', '')}"
        working.pop(SESSION_ID_KEY, None)
        working.pop(CHECKPOINT_MATERIALS_KEY, None)
        if linked:
            for collection in source.users_collection or (context.collection,):
                collection.objects.link(working)
            self.select_only(context, working)
        return working

    @staticmethod
    def dependency_snapshot():
        return {
            "objects": set(bpy.data.objects),
            "meshes": set(bpy.data.meshes),
            "materials": set(bpy.data.materials),
            "images": set(bpy.data.images),
            "node_groups": set(bpy.data.node_groups),
            "textures": set(bpy.data.textures),
        }

    @staticmethod
    def remove_new_orphans(before):
        # Remove owners before their mesh/material/image dependencies.
        for name in ("objects", "meshes", "materials", "node_groups", "textures", "images"):
            collection = getattr(bpy.data, name)
            new_items = [item for item in collection if item not in before[name]]
            for item in new_items:
                if item.users == 0:
                    if name == "objects":
                        collection.remove(item, do_unlink=True)
                    else:
                        collection.remove(item)

    @staticmethod
    def object_dependencies(obj):
        """Collect data blocks that may become orphaned when ``obj`` is swapped."""
        dependencies = {
            "materials": set(),
            "images": set(),
            "node_groups": set(),
            "textures": set(),
        }
        if not obj or obj.type != "MESH":
            return dependencies
        node_trees = []
        for material in obj.data.materials:
            if not material:
                continue
            dependencies["materials"].add(material)
            if material.node_tree:
                node_trees.append(material.node_tree)
        seen_trees = set()
        while node_trees:
            tree = node_trees.pop()
            if tree in seen_trees:
                continue
            seen_trees.add(tree)
            for node in tree.nodes:
                image = getattr(node, "image", None)
                if image:
                    dependencies["images"].add(image)
                node_tree = getattr(node, "node_tree", None)
                if node_tree:
                    dependencies["node_groups"].add(node_tree)
                    node_trees.append(node_tree)
        return dependencies

    @staticmethod
    def remove_orphan_dependencies(dependencies):
        for name in ("materials", "node_groups", "textures", "images"):
            collection = getattr(bpy.data, name)
            for item in list(dependencies[name]):
                if item.users == 0:
                    collection.remove(item)

    def append_checkpoint(self, disk, slot: str, *, expected_name: str):
        """Append one checkpoint object and reuse matching in-memory materials."""
        dependencies_before = self.dependency_snapshot()
        materials_before = {material.name: material for material in bpy.data.materials}
        restored, manifest = disk.load(slot, expected_name=expected_name)

        try:
            material_names = json.loads(restored.get(CHECKPOINT_MATERIALS_KEY, "[]"))
        except (TypeError, ValueError):
            material_names = []
        restored.pop(CHECKPOINT_MATERIALS_KEY, None)
        for index, material_name in enumerate(material_names):
            material = materials_before.get(material_name)
            if material and index < len(restored.data.materials):
                restored.data.materials[index] = material
        return restored, dependencies_before, manifest

    def load_checkpoint(
        self,
        context,
        disk,
        slot: str,
        *,
        current,
        session_id: str,
        expected_name: str,
    ):
        """Replace the session object with the object in one recovery slot."""
        current_dependencies = self.object_dependencies(current)
        restored, dependencies_before, manifest = self.append_checkpoint(
            disk,
            slot,
            expected_name=expected_name,
        )

        # Loading a child object also loads a private copy of its parent. Point
        # the checkpoint back at the existing scene parent before orphan cleanup.
        loaded_parent = restored.parent
        if loaded_parent and loaded_parent not in dependencies_before["objects"]:
            parent_name = str(manifest.get("parent_name", ""))
            existing_parent = current.parent
            if existing_parent is None and parent_name:
                existing_parent = next(
                    (obj for obj in dependencies_before["objects"] if obj.name == parent_name),
                    None,
                )
            if existing_parent is not None:
                parent_inverse = restored.matrix_parent_inverse.copy()
                matrix_basis = restored.matrix_basis.copy()
                restored.parent = existing_parent
                restored.matrix_parent_inverse = parent_inverse
                restored.matrix_basis = matrix_basis

        name = current.name
        collections = list(current.users_collection) or [context.collection]
        for collection in collections:
            if restored.name not in collection.objects:
                collection.objects.link(restored)

        self.remove(current)
        self.remove_orphan_dependencies(current_dependencies)
        self.remove_new_orphans(dependencies_before)
        restored.name = name
        restored[SESSION_ID_KEY] = session_id
        self.select_only(context, restored)
        return restored

    def load_temporary_source(self, context, disk, *, expected_name: str):
        """Load the source checkpoint as a disposable object for texture baking."""
        restored, dependencies_before, _manifest = self.append_checkpoint(
            disk,
            "source",
            expected_name=expected_name,
        )
        restored.pop(SESSION_ID_KEY, None)
        restored.name = "_remi_source_checkpoint"
        context.collection.objects.link(restored)
        return restored, dependencies_before

    def cleanup_temporary_source(self, source, dependencies_before):
        """Discard a checkpoint load and all dependencies it introduced."""
        try:
            exists = source is not None and source.name in bpy.data.objects
        except ReferenceError:
            exists = False
        if exists:
            source_dependencies = self.object_dependencies(source)
            self.remove(source)
            self.remove_orphan_dependencies(source_dependencies)
        self.remove_new_orphans(dependencies_before)
