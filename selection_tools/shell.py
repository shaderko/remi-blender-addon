"""Double-shell detection and removal operators."""

from collections import deque
import math

import bmesh
import bpy
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree


class Remi_OT_DoubleShellBase:
    """Detect nearby opposite-facing layers in double-shell AI geometry."""

    max_thickness_ratio: bpy.props.FloatProperty(
        name="Max Shell Gap",
        description="Maximum layer separation as a fraction of the mesh diagonal",
        min=0.0001, max=0.25, default=0.035, precision=4, subtype="FACTOR",
    )
    opposite_angle: bpy.props.FloatProperty(
        name="Opposite Angle °",
        description="Minimum normal angle used to recognize opposing surface layers",
        min=90.0, max=180.0, default=135.0,
    )
    propagation_angle: bpy.props.FloatProperty(
        name="Surface Continuity °",
        description="Maximum angle across which inner-shell selection may propagate",
        min=1.0, max=179.0, default=70.0,
    )
    include_connectors: bpy.props.BoolProperty(
        name="Include Connectors",
        description="Include side-wall faces that directly join detected inner and outer layers",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and context.mode == "EDIT_MESH"

    @staticmethod
    def _clear_selection(bm):
        for vertex in bm.verts:
            vertex.select = False
        for edge in bm.edges:
            edge.select = False
        for face in bm.faces:
            face.select = False

    @staticmethod
    def _surface_patches(faces, continuity_dot):
        """Group adjacent, similarly oriented faces into coherent surfaces."""
        visible = {face.index for face in faces}
        face_to_patch = {}
        patches = []
        for seed in faces:
            if seed.index in face_to_patch:
                continue
            patch_index = len(patches)
            patch = set()
            queue = deque([seed])
            face_to_patch[seed.index] = patch_index
            while queue:
                face = queue.popleft()
                patch.add(face.index)
                for edge in face.edges:
                    for neighbor in edge.link_faces:
                        neighbor_index = neighbor.index
                        if neighbor_index not in visible or neighbor_index in face_to_patch:
                            continue
                        if face.normal.dot(neighbor.normal) < continuity_dot:
                            continue
                        face_to_patch[neighbor_index] = patch_index
                        queue.append(neighbor)
            patches.append(patch)
        return face_to_patch, patches

    @staticmethod
    def _patch_metrics(bm, patches, face_centers, center):
        metrics = []
        for patch in patches:
            area_sum = 0.0
            radius_sum = 0.0
            orientation_sum = 0.0
            for face_index in patch:
                face = bm.faces[face_index]
                area = max(1e-12, face.calc_area())
                radial = face_centers[face_index] - center
                area_sum += area
                radius_sum += area * radial.length
                if radial.length_squared > 1e-18:
                    orientation_sum += area * face.normal.dot(radial.normalized())
            metrics.append(
                {
                    "area": area_sum,
                    "radius": radius_sum / max(1e-12, area_sum),
                    "orientation": orientation_sum / max(1e-12, area_sum),
                }
            )
        return metrics

    def _detect_inner_shell(self, bm):
        bm.normal_update()
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        faces = [face for face in bm.faces if not face.hide]
        if len(faces) < 8:
            return None

        coordinates = [vertex.co for vertex in bm.verts if not vertex.hide]
        if not coordinates:
            return None
        center = sum(coordinates, coordinates[0] * 0.0) / len(coordinates)
        min_co = coordinates[0].copy()
        max_co = coordinates[0].copy()
        for coordinate in coordinates[1:]:
            min_co.x = min(min_co.x, coordinate.x)
            min_co.y = min(min_co.y, coordinate.y)
            min_co.z = min(min_co.z, coordinate.z)
            max_co.x = max(max_co.x, coordinate.x)
            max_co.y = max(max_co.y, coordinate.y)
            max_co.z = max(max_co.z, coordinate.z)
        diagonal = max(1e-9, (max_co - min_co).length)
        max_gap = diagonal * float(self.max_thickness_ratio)
        opposite_dot = math.cos(math.radians(float(self.opposite_angle)))
        continuity_dot = math.cos(math.radians(float(self.propagation_angle)))

        face_centers = {face.index: face.calc_center_median() for face in faces}
        visible = {face.index for face in faces}
        face_to_patch, patches = self._surface_patches(faces, continuity_dot)
        patch_metrics = self._patch_metrics(bm, patches, face_centers, center)

        tree = KDTree(len(faces))
        for face in faces:
            tree.insert(face_centers[face.index], face.index)
        tree.balance()

        # A ray cast along the back of each face finds the opposing layer even
        # when the two sides have unrelated triangle sizes and centroids.  The
        # KD fallback retains support for noisy scans whose layers are not
        # perfectly aligned along the face normal.
        ray_epsilon = max(1e-8, diagonal * 1e-7)
        surface_bvh = BVHTree.FromBMesh(bm, epsilon=ray_epsilon)
        matched_pairs = set()
        for face in faces:
            center_a = face_centers[face.index]
            partner = None
            direction = -face.normal
            hit = surface_bvh.ray_cast(
                center_a + direction * ray_epsilon,
                direction,
                max_gap,
            )
            if hit[2] is not None:
                other_index = int(hit[2])
                if (
                    other_index != face.index
                    and other_index in visible
                    and face_to_patch[other_index] != face_to_patch[face.index]
                    and face.normal.dot(bm.faces[other_index].normal) <= opposite_dot
                ):
                    partner = other_index

            if partner is None:
                best = None
                for _, other_index, distance in tree.find_n(center_a, min(64, len(faces))):
                    if (
                        other_index == face.index
                        or distance > max_gap
                        or face_to_patch[other_index] == face_to_patch[face.index]
                    ):
                        continue
                    other = bm.faces[other_index]
                    normal_dot = face.normal.dot(other.normal)
                    if normal_dot > opposite_dot:
                        continue
                    delta = face_centers[other_index] - center_a
                    if delta.length_squared <= 1e-18:
                        continue
                    pair_direction = delta.normalized()
                    # On a true double layer, each face points away from its
                    # counterpart. Reject lateral neighbors that only happen
                    # to have opposing normals on a folded surface.
                    facing = min(
                        -face.normal.dot(pair_direction),
                        other.normal.dot(pair_direction),
                    )
                    if facing < 0.15:
                        continue
                    score = (
                        distance / max_gap
                        + 0.35 * (1.0 + normal_dot)
                        + 0.35 * (1.0 - facing)
                    )
                    if best is None or score < best[0]:
                        best = (score, other_index)
                if best is not None:
                    partner = best[1]

            if partner is None:
                continue
            matched_pairs.add(tuple(sorted((face.index, partner))))

        minimum_pairs = max(4, min(32, len(faces) // 100))
        if len(matched_pairs) < minimum_pairs:
            return None

        # Consolidate pair evidence at the surface-patch level.  This is the
        # important distinction from the old per-face seed flood: one noisy
        # pair can no longer punch a zigzag hole through an otherwise coherent
        # inner surface.
        relations = {}
        for face_a, face_b in matched_pairs:
            patch_a = face_to_patch[face_a]
            patch_b = face_to_patch[face_b]
            if patch_a == patch_b:
                continue
            relation = tuple(sorted((patch_a, patch_b)))
            weight = min(
                max(1e-12, bm.faces[face_a].calc_area()),
                max(1e-12, bm.faces[face_b].calc_area()),
            )
            relations[relation] = relations.get(relation, 0.0) + weight

        if not relations:
            return None

        inner_votes = {patch_index: 0.0 for patch_index in range(len(patches))}
        outer_votes = {patch_index: 0.0 for patch_index in range(len(patches))}
        for (patch_a, patch_b), weight in relations.items():
            metrics_a = patch_metrics[patch_a]
            metrics_b = patch_metrics[patch_b]
            orientation_a = metrics_a["orientation"]
            orientation_b = metrics_b["orientation"]

            # Complete nested surfaces have opposite signed radial orientation:
            # the outer layer points away from the object and the inner layer
            # points into its cavity. Radius is the stable fallback for partial
            # or nearly tangent patches.
            if orientation_a <= -0.05 and orientation_b >= 0.05:
                inner, outer = patch_a, patch_b
            elif orientation_b <= -0.05 and orientation_a >= 0.05:
                inner, outer = patch_b, patch_a
            elif abs(metrics_a["radius"] - metrics_b["radius"]) > diagonal * 1e-6:
                inner, outer = (
                    (patch_a, patch_b)
                    if metrics_a["radius"] < metrics_b["radius"]
                    else (patch_b, patch_a)
                )
            else:
                inner, outer = (
                    (patch_a, patch_b)
                    if orientation_a < orientation_b
                    else (patch_b, patch_a)
                )
            inner_votes[inner] += weight
            outer_votes[outer] += weight

        inner_patches = {
            patch_index
            for patch_index in range(len(patches))
            if inner_votes[patch_index] > outer_votes[patch_index]
        }
        outer_patches = {
            patch_index
            for patch_index in range(len(patches))
            if outer_votes[patch_index] > inner_votes[patch_index]
        }
        if not inner_patches or not outer_patches:
            return None

        inner_faces = {
            face_index
            for patch_index in inner_patches
            for face_index in patches[patch_index]
        }

        connector_faces = set()
        if self.include_connectors:
            patch_adjacency = {patch_index: set() for patch_index in range(len(patches))}
            for face in faces:
                patch_a = face_to_patch[face.index]
                for edge in face.edges:
                    for neighbor in edge.link_faces:
                        if neighbor.index not in visible:
                            continue
                        patch_b = face_to_patch[neighbor.index]
                        if patch_a != patch_b:
                            patch_adjacency[patch_a].add(patch_b)

            # Connector walls can themselves be split into several sharp
            # patches. Include a whole neutral patch component only when it
            # topologically bridges a classified inner and outer surface.
            neutral = set(range(len(patches))) - inner_patches - outer_patches
            while neutral:
                start = neutral.pop()
                component = {start}
                queue = deque([start])
                boundary = set()
                while queue:
                    patch_index = queue.popleft()
                    for neighbor in patch_adjacency[patch_index]:
                        if neighbor in neutral:
                            neutral.remove(neighbor)
                            component.add(neighbor)
                            queue.append(neighbor)
                        elif neighbor not in component:
                            boundary.add(neighbor)
                if boundary & inner_patches and boundary & outer_patches:
                    connector_faces.update(
                        face_index
                        for patch_index in component
                        for face_index in patches[patch_index]
                    )

        selected = inner_faces | connector_faces
        if not selected or len(selected) >= int(len(faces) * 0.85):
            return None
        return selected, len(matched_pairs), len(connector_faces), max_gap


class Remi_OT_SelectInnerShell(Remi_OT_DoubleShellBase, bpy.types.Operator):
    """Preview the inner layer of double-sided AI-generated geometry."""

    bl_idname = "remi.select_inner_shell"
    bl_label = "Select Inner Shell"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        result = self._detect_inner_shell(bm)
        if result is None:
            self.report({"WARNING"}, "No confident double shell found; adjust Max Shell Gap in the redo panel")
            return {"CANCELLED"}
        selected, pair_count, connector_count, _ = result
        self._clear_selection(bm)
        for face_index in selected:
            bm.faces[face_index].select = True
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        bpy.ops.mesh.select_mode(type="FACE")
        self.report(
            {"INFO"},
            f"Inner shell selected: {len(selected)} faces, {pair_count} opposing pairs, {connector_count} connectors",
        )
        return {"FINISHED"}


class Remi_OT_RemoveInnerShell(Remi_OT_DoubleShellBase, bpy.types.Operator):
    """Remove the detected inner layer while retaining the outer surface."""

    bl_idname = "remi.remove_inner_shell"
    bl_label = "Remove Inner Shell"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        selected = {face.index for face in bm.faces if face.select}
        pair_count = connector_count = 0
        used_preview = 4 <= len(selected) < int(len(bm.faces) * 0.85)
        if not used_preview:
            result = self._detect_inner_shell(bm)
            if result is None:
                self.report({"WARNING"}, "No confident double shell found; use Select Inner Shell to tune detection first")
                return {"CANCELLED"}
            selected, pair_count, connector_count, _ = result
        faces_to_delete = [bm.faces[index] for index in selected if index < len(bm.faces)]
        bmesh.ops.delete(bm, geom=faces_to_delete, context="FACES")
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        detail = "using the preview selection" if used_preview else f"from {pair_count} opposing pairs ({connector_count} connectors)"
        self.report({"INFO"}, f"Removed inner shell: {len(faces_to_delete)} faces {detail}")
        return {"FINISHED"}
