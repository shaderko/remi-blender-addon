"""Edit Mode selection and bridge-splitting tools for Remi."""

from collections import deque

import bmesh
import bpy


class Remi_OT_BridgeBase:
    """Shared lobe/bridge analysis used by Remi's Edit Mode tools."""

    expand_steps: bpy.props.IntProperty(
        name="Expand Steps",
        description="Expand detected bridge by adjacent face rings",
        min=0, max=8, default=0,
    )
    seed_samples: bpy.props.IntProperty(
        name="Seed Samples",
        description="Candidate lobe pairs evaluated while finding a bridge",
        min=4, max=48, default=18,
    )
    min_face_ratio: bpy.props.FloatProperty(
        name="Min Face Ratio",
        description="Minimum smaller/larger face ratio accepted as a lobe split",
        min=0.0, max=1.0, default=0.08, subtype="FACTOR",
    )
    min_volume_ratio: bpy.props.FloatProperty(
        name="Min Volume Ratio",
        description="Minimum smaller/larger bounding-volume proxy accepted as a lobe split",
        min=0.0, max=1.0, default=0.06, subtype="FACTOR",
    )
    mark_seam: bpy.props.BoolProperty(
        name="Mark Seams",
        description="Mark the detected bridge edges as seams",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and context.mode == "EDIT_MESH"

    @staticmethod
    def _dual_graph(bm, faces):
        adjacency = {face_index: set() for face_index in faces}
        edge_by_pair = {}
        for face_index in faces:
            for edge in bm.faces[face_index].edges:
                linked = [face for face in edge.link_faces if face.index in faces]
                if len(linked) != 2:
                    continue
                first, second = linked[0].index, linked[1].index
                key = (first, second) if first < second else (second, first)
                adjacency[first].add(second)
                adjacency[second].add(first)
                edge_by_pair.setdefault(key, edge)
        return adjacency, edge_by_pair

    @staticmethod
    def _components(nodes, adjacency):
        unvisited = set(nodes)
        components = []
        while unvisited:
            start = next(iter(unvisited))
            queue = deque([start])
            unvisited.remove(start)
            component = []
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbor in adjacency.get(current, ()):
                    if neighbor in unvisited:
                        unvisited.remove(neighbor)
                        queue.append(neighbor)
            components.append(component)
        return sorted(components, key=len, reverse=True)

    @staticmethod
    def _distances(start, allowed, adjacency):
        distances = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency.get(current, ()):
                if neighbor in allowed and neighbor not in distances:
                    distances[neighbor] = distances[current] + 1
                    queue.append(neighbor)
        return distances

    def _farthest_seeds(self, component, adjacency, first_seed):
        seeds = [first_seed]
        min_distances = {face: 10 ** 9 for face in component}
        for face, distance in self._distances(first_seed, component, adjacency).items():
            min_distances[face] = distance
        while len(seeds) < min(int(self.seed_samples), len(component)):
            candidate = max(component, key=lambda face: min_distances[face] if face not in seeds else -1)
            if candidate in seeds:
                break
            seeds.append(candidate)
            for face, distance in self._distances(candidate, component, adjacency).items():
                min_distances[face] = min(min_distances[face], distance)
        return seeds

    @staticmethod
    def _volume_proxy(bm, faces):
        if not faces:
            return 0.0
        min_co = max_co = None
        area = 0.0
        used_vertices = set()
        for face_index in faces:
            face = bm.faces[face_index]
            area += face.calc_area()
            for vertex in face.verts:
                if vertex.index in used_vertices:
                    continue
                used_vertices.add(vertex.index)
                if min_co is None:
                    min_co = max_co = vertex.co.copy()
                else:
                    min_co.x = min(min_co.x, vertex.co.x)
                    min_co.y = min(min_co.y, vertex.co.y)
                    min_co.z = min(min_co.z, vertex.co.z)
                    max_co.x = max(max_co.x, vertex.co.x)
                    max_co.y = max(max_co.y, vertex.co.y)
                    max_co.z = max(max_co.z, vertex.co.z)
        return max(1e-12, area * max(1e-6, (max_co - min_co).length))

    def _partition(self, bm, component, adjacency, edge_by_pair):
        """Find the least disruptive balanced face-graph cut between two lobes."""
        start = next(iter(component))
        first_distances = self._distances(start, component, adjacency)
        seed_a = max(first_distances, key=first_distances.get)
        distances_a = self._distances(seed_a, component, adjacency)
        candidates = self._farthest_seeds(component, adjacency, seed_a)[1:]
        best = None
        infinity = 10 ** 9

        for seed_b in candidates:
            distances_b = self._distances(seed_b, component, adjacency)
            part_a = {face for face in component if distances_a.get(face, infinity) <= distances_b.get(face, infinity)}
            part_b = component - part_a
            if not part_a or not part_b:
                continue
            face_ratio = min(len(part_a), len(part_b)) / max(len(part_a), len(part_b))
            volume_a = self._volume_proxy(bm, part_a)
            volume_b = self._volume_proxy(bm, part_b)
            volume_ratio = min(volume_a, volume_b) / max(volume_a, volume_b)
            bridge_pairs = [
                pair for pair in edge_by_pair
                if (pair[0] in part_a) != (pair[1] in part_a)
            ]
            if not bridge_pairs:
                continue
            # Favor a small neck, but avoid splitting off insignificant details.
            score = len(bridge_pairs) / min(len(part_a), len(part_b)) + (1.0 - face_ratio) * 0.9 + (1.0 - volume_ratio) * 1.4
            candidate = (score, part_a, part_b, bridge_pairs, seed_a, seed_b, face_ratio, volume_ratio)
            if best is None or candidate[0] < best[0]:
                best = candidate

        if best is None:
            return None
        _, part_a, part_b, bridge_pairs, seed_a, seed_b, face_ratio, volume_ratio = best
        return (
            part_a,
            part_b,
            {edge_by_pair[pair] for pair in bridge_pairs},
            seed_a,
            seed_b,
            face_ratio,
            volume_ratio,
        )

    @staticmethod
    def _clear_selection(bm):
        for vertex in bm.verts:
            vertex.select = False
        for edge in bm.edges:
            edge.select = False
        for face in bm.faces:
            face.select = False

    def _selected_component(self, bm):
        selected = {face.index for face in bm.faces if face.select}
        if len(selected) < 4:
            return None
        adjacency, edge_by_pair = self._dual_graph(bm, selected)
        components = self._components(selected, adjacency)
        if not components:
            return None
        component = set(components[0])
        return component, adjacency, edge_by_pair, len(components)

    def _expand_edges(self, edges, component):
        expanded = set(edges)
        for _ in range(self.expand_steps):
            ring_faces = {
                face
                for edge in expanded
                for face in edge.link_faces
                if face.index in component
            }
            for face in ring_faces:
                expanded.update(
                    edge for edge in face.edges
                    if any(linked.index in component for linked in edge.link_faces)
                )
        return expanded

    def _valid_partition(self, partition):
        if partition is None:
            self.report({"ERROR"}, "Could not find a bridge between two face lobes")
            return False
        if partition[5] < self.min_face_ratio or partition[6] < self.min_volume_ratio:
            self.report(
                {"ERROR"},
                "Split rejected as unbalanced. Narrow the selection or lower Min Face/Volume Ratio in the redo panel.",
            )
            return False
        return True


class Remi_OT_DetectBridge(Remi_OT_BridgeBase, bpy.types.Operator):
    """Select connector edges between the two main regions of the face selection."""

    bl_idname = "remi.detect_bridge"
    bl_label = "Detect Bridge"
    bl_options = {"REGISTER", "UNDO"}

    clear_selection: bpy.props.BoolProperty(
        name="Clear Selection",
        description="Clear the face selection before selecting bridge edges",
        default=True,
    )

    def execute(self, context):
        bm = bmesh.from_edit_mesh(context.active_object.data)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        data = self._selected_component(bm)
        if data is None:
            self.report({"ERROR"}, "Select at least four connected faces")
            return {"CANCELLED"}
        component, adjacency, edge_by_pair, count = data
        partition = self._partition(bm, component, adjacency, edge_by_pair)
        if not self._valid_partition(partition):
            return {"CANCELLED"}
        _, _, edges, _, _, _, _ = partition
        edges = self._expand_edges(edges, component)
        if self.clear_selection:
            self._clear_selection(bm)
        for edge in edges:
            edge.select = True
            edge.seam = edge.seam or self.mark_seam
        bmesh.update_edit_mesh(context.active_object.data, loop_triangles=False, destructive=False)
        bpy.ops.mesh.select_mode(type="EDGE")
        suffix = " (largest connected selection used)" if count > 1 else ""
        self.report({"INFO"}, f"Bridge edges selected: {len(edges)}{suffix}")
        return {"FINISHED"}


class Remi_OT_SelectSplitPart(Remi_OT_BridgeBase, bpy.types.Operator):
    """Select the face lobe that Split By Bridge would turn into a new object."""

    bl_idname = "remi.select_split_part"
    bl_label = "Select Split Part"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bm = bmesh.from_edit_mesh(context.active_object.data)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        data = self._selected_component(bm)
        if data is None:
            self.report({"ERROR"}, "Select at least four connected faces")
            return {"CANCELLED"}
        component, adjacency, edge_by_pair, _ = data
        partition = self._partition(bm, component, adjacency, edge_by_pair)
        if not self._valid_partition(partition):
            return {"CANCELLED"}
        part_a, _, _, _, _, _, _ = partition
        self._clear_selection(bm)
        for index in part_a:
            bm.faces[index].select = True
        bmesh.update_edit_mesh(context.active_object.data, loop_triangles=False, destructive=False)
        bpy.ops.mesh.select_mode(type="FACE")
        self.report({"INFO"}, f"Selected split part faces: {len(part_a)}")
        return {"FINISHED"}


class Remi_OT_SplitByBridge(Remi_OT_BridgeBase, bpy.types.Operator):
    """Separate one lobe of the selected mesh by its detected bridge."""

    bl_idname = "remi.split_by_bridge"
    bl_label = "Split By Bridge"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bm = bmesh.from_edit_mesh(context.active_object.data)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        data = self._selected_component(bm)
        if data is None:
            self.report({"ERROR"}, "Select at least four connected faces")
            return {"CANCELLED"}
        component, adjacency, edge_by_pair, _ = data
        partition = self._partition(bm, component, adjacency, edge_by_pair)
        if not self._valid_partition(partition):
            return {"CANCELLED"}
        part_a, _, edges, _, _, _, _ = partition
        self._clear_selection(bm)
        for index in part_a:
            bm.faces[index].select = True
        if self.mark_seam:
            for edge in edges:
                edge.seam = True
        bmesh.update_edit_mesh(context.active_object.data, loop_triangles=False, destructive=False)
        bpy.ops.mesh.select_mode(type="FACE")
        before = {obj.name for obj in bpy.data.objects}
        result = bpy.ops.mesh.separate(type="SELECTED")
        if "FINISHED" not in result:
            self.report({"ERROR"}, "Could not separate the selected split part")
            return {"CANCELLED"}
        created = [obj for obj in bpy.data.objects if obj.name not in before and obj.type == "MESH"]
        self.report({"INFO"}, f"Bridge split complete: {len(created)} object created")
        return {"FINISHED"}


class Remi_OT_SmartSelectObject(Remi_OT_BridgeBase, bpy.types.Operator):
    """Select the lobe containing the currently selected face, edge, or vertex."""

    bl_idname = "remi.smart_select_object"
    bl_label = "Smart Select Object"
    bl_options = {"REGISTER", "UNDO"}

    refine_steps: bpy.props.IntProperty(
        name="Refine Steps",
        description="How many times to refine the lobe containing the picked element",
        min=0, max=8, default=2,
    )

    @staticmethod
    def _seed_face(bm):
        if bm.faces.active and bm.faces.active.select:
            return bm.faces.active.index
        for face in bm.faces:
            if face.select:
                return face.index
        for edge in bm.edges:
            if edge.select and edge.link_faces:
                return edge.link_faces[0].index
        for vertex in bm.verts:
            if vertex.select and vertex.link_faces:
                return vertex.link_faces[0].index
        return None

    def execute(self, context):
        bm = bmesh.from_edit_mesh(context.active_object.data)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        seed = self._seed_face(bm)
        if seed is None:
            self.report({"ERROR"}, "Select a face, edge, or vertex to identify the desired lobe")
            return {"CANCELLED"}
        component = {face.index for face in bm.faces}
        adjacency, all_edges = self._dual_graph(bm, component)
        for _ in range(self.refine_steps):
            if len(component) < 8:
                break
            local_adjacency = {face: adjacency[face] & component for face in component}
            local_edges = {pair: edge for pair, edge in all_edges.items() if pair[0] in component and pair[1] in component}
            partition = self._partition(bm, component, local_adjacency, local_edges)
            if (
                partition is None
                or partition[5] < self.min_face_ratio
                or partition[6] < self.min_volume_ratio
            ):
                break
            part_a, part_b, _, _, _, _, _ = partition
            next_component = part_a if seed in part_a else part_b
            if len(next_component) == len(component):
                break
            component = next_component
        self._clear_selection(bm)
        for index in component:
            bm.faces[index].select = True
        bmesh.update_edit_mesh(context.active_object.data, loop_triangles=False, destructive=False)
        bpy.ops.mesh.select_mode(type="FACE")
        self.report({"INFO"}, f"Smart selected faces: {len(component)}")
        return {"FINISHED"}


classes = (
    Remi_OT_DetectBridge,
    Remi_OT_SelectSplitPart,
    Remi_OT_SplitByBridge,
    Remi_OT_SmartSelectObject,
)

# Blender reads RNA properties from the concrete registered class.  Copy the
# shared analysis controls into each operator while keeping the implementation
# itself as a plain Python mixin.
for _operator_class in classes:
    _operator_class.__annotations__ = {
        **Remi_OT_BridgeBase.__annotations__,
        **getattr(_operator_class, "__annotations__", {}),
    }


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
