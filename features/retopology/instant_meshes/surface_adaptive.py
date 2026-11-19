"""Source-driven patch quadrangulation, independent of the Instant Meshes grid.

Grow disk-like, approximately planar source regions, resample their *shared*
interfaces, then quadrangulate convex cells. Large smooth regions lose their
internal grid altogether. No coordinate welding, triangle-to-quad guessing, or
modification of Blender IDs. Finite surface samples are guards, not a global
Hausdorff/self-intersection certificate. This is not a deformation-loop solver.
"""

from collections import deque
import math
import time

import numpy as np

from .adaptive import SAMPLE_WEIGHTS, QuadSimplifier, face_normals, triangle_distances
from .quality import mesh_quality, output_edges


MAX_SOURCE_FACES = 200000
MAX_OUTPUT_FACES = 25000


class _RefinePatch(ValueError):
    def __init__(self, members, reason):
        super().__init__(reason)
        self.members = members


def _cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def _simplify_chain(vertices, chain, tolerance):
    """RDP on original 3D interface samples, with common junctions retained."""
    chain = list(chain)
    if len(chain) <= 2:
        return set(chain)
    if chain[0] == chain[-1]:
        points = vertices[chain[:-1]]
        split = int(np.linalg.norm(points-points[0], axis=1).argmax())
        if split == 0:
            return set(chain)
        kept = (_simplify_chain(vertices, chain[:split+1], tolerance) |
                _simplify_chain(vertices, chain[split:], tolerance))
        if len(kept) < 3:
            edge = points[split]-points[0]
            distance = np.linalg.norm(np.cross(points-points[0], edge), axis=1)
            kept.add(chain[int(distance.argmax())])
        return kept
    points = vertices[chain]
    keep, stack = {0, len(chain)-1}, [(0, len(chain)-1)]
    while stack:
        first, last = stack.pop()
        if last-first <= 1:
            continue
        edge = points[last]-points[first]
        delta = points[first+1:last]-points[first]
        t = np.clip(delta @ edge/max(float(edge @ edge), 1e-30), 0, 1)
        distance = np.linalg.norm(delta-t[:, None]*edge, axis=1)
        offset = int(distance.argmax())
        if distance[offset] > tolerance:
            middle = first+1+offset
            keep.add(middle)
            stack.extend(((first, middle), (middle, last)))
    return {chain[index] for index in keep}


def _loop(boundary):
    following = {}
    for a, b in boundary:
        if a in following:
            raise ValueError("A patch boundary touches itself")
        following[a] = b
    if not following:
        raise ValueError("A patch has no boundary")
    first = min(following)
    values, visited, current = [], set(), first
    while current not in visited:
        values.append(current)
        visited.add(current)
        current = following[current]
    if current != first or len(values) != len(following):
        raise ValueError("A patch has more than one boundary loop")
    return values


def _convex_cells(polygon, coordinates, area_floor, valid_cell=lambda values: True):
    """Ear clipping for concavities; convex regions need no seed triangulation."""
    area_floor = max(area_floor, float(np.linalg.norm(np.ptp(coordinates[polygon], axis=0))**2)*1e-7)
    def convex(values):
        p = coordinates[values]
        return all(_cross(b-a, c-b) > area_floor for a, b, c in
                   zip(np.roll(p, 1, axis=0), p, np.roll(p, -1, axis=0)))

    def split(values):
        if len(values) <= 8:
            return [values]
        middle = len(values)//2
        return split(values[:middle+1])+split(values[middle:]+values[:1])

    if convex(polygon):
        cells = split(polygon)
        if all(valid_cell(cell) for cell in cells):
            return cells
    if len(polygon) > 256:
        raise ValueError("Patch interface exceeds the experimental polygon limit")
    remaining, triangles = list(polygon), []
    while len(remaining) > 3:
        found = False
        for index, b in enumerate(remaining):
            a, c = remaining[index-1], remaining[(index+1) % len(remaining)]
            pa, pb, pc = coordinates[[a, b, c]]
            if _cross(pb-pa, pc-pb) <= area_floor:
                continue
            if not valid_cell([a, b, c]):
                continue
            if len(remaining) == 4:
                final = [v for v in remaining if v != b]
                if not convex(final) or not valid_cell(final):
                    continue
            other = [v for v in remaining if v not in {a, b, c}]
            p = coordinates[other]
            inside = ((_cross_vector(pb-pa, p-pa) >= -area_floor) &
                      (_cross_vector(pc-pb, p-pb) >= -area_floor) &
                      (_cross_vector(pa-pc, p-pc) >= -area_floor))
            if inside.any():
                continue
            triangles.append([a, b, c])
            remaining.pop(index)
            found = True
            break
        if not found:
            raise ValueError("A simplified patch cannot be triangulated without folded cells")
    if not convex(remaining) or not valid_cell(remaining):
        raise ValueError("A patch would leave a folded/sliver triangle")
    triangles.append(remaining)
    # Merge convex neighboring triangles before subdivision, avoiding needless
    # valence-three centres on rectilinear patches and inherited seed diagonals.
    cells = []
    while triangles:
        first = triangles.pop(0)
        merged = False
        for index, second in enumerate(triangles):
            if len(set(first) & set(second)) != 2:
                continue
            boundary = set(zip(first, first[1:]+first[:1]))
            for edge in zip(second, second[1:]+second[:1]):
                reverse = edge[::-1]
                if reverse in boundary:
                    boundary.remove(reverse)
                else:
                    boundary.add(edge)
            values = _loop(boundary)
            if len(values) == 4 and convex(values) and valid_cell(values):
                cells.append(values)
                triangles.pop(index)
                merged = True
                break
        if not merged:
            cells.append(first)
    return cells


def _cross_vector(a, b):
    return a[0]*b[:, 1]-a[1]*b[:, 0]


def _output_trees(quads):
    try:
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree
    except ImportError:
        return None
    vertices = [Vector(point) for point in quads.reshape(-1, 3)]
    indices = np.arange(len(vertices)).reshape(-1, 4)
    return [BVHTree.FromPolygons(vertices, indices[:, pattern].reshape(-1, 3).tolist(), all_triangles=True)
            for pattern in ([[0, 1, 2], [0, 2, 3]], [[0, 1, 3], [1, 2, 3]])]


def _reverse_error(samples, quads, trees=None):
    """Test source witnesses against BOTH possible output quad diagonals."""
    if trees is not None:
        from mathutils import Vector
        return max(float(tree.find_nearest(Vector(point))[3]) for tree in trees for point in samples)
    maximum = 0.0
    for pattern in ([[0, 1, 2], [0, 2, 3]], [[0, 1, 3], [1, 2, 3]]):
        triangles = quads[:, pattern].reshape(-1, 3, 3)
        for begin in range(0, len(samples), 64):
            distances = triangle_distances(samples[begin:begin+64], triangles).min(axis=1)
            maximum = max(maximum, float(np.sqrt(distances.max())))
    return maximum


def _boundary_components(vertices, faces):
    edges, counts, _ = output_edges(faces)
    boundary = edges[counts == 1]
    neighbors = {}
    for a, b in boundary:
        neighbors.setdefault(int(a), set()).add(int(b))
        neighbors.setdefault(int(b), set()).add(int(a))
    if any(len(items) != 2 for items in neighbors.values()):
        raise ValueError("The source/output has a non-manifold boundary")
    remaining, components = set(neighbors), 0
    while remaining:
        stack = [min(remaining)]
        components += 1
        while stack:
            vertex = stack.pop()
            if vertex in remaining:
                remaining.remove(vertex)
                stack.extend(neighbors[vertex] & remaining)
    return components


def _fit_plane(points, orientation):
    centre = points.mean(axis=0)
    delta = points-centre
    _values, vectors = np.linalg.eigh(delta.T @ delta)
    normal = vectors[:, 0]
    if normal @ orientation < 0:
        normal = -normal
    return centre, normal, float(np.max(np.abs(delta @ normal)))


def _transition_edges(cells):
    """Split only where transitions require it, not every smooth-region edge.

    Non-quad cells use a midpoint fan. Quads keep 0/2/4 split edges. A dual
    spanning forest pairs odd split parities, including an outside node for
    open boundaries. This prevents T-junctions without subdividing the entire
    coarse quad net just because a small detailed region contains triangles.
    """
    owners = {}
    for index, cell in enumerate(cells):
        for a, b in zip(cell, cell[1:]+cell[:1]):
            owners.setdefault(tuple(sorted((a, b))), []).append(index)
    if any(len(items) > 2 for items in owners.values()):
        raise ValueError("Coarse patch cells have a non-manifold interface")
    split = {edge for edge, items in owners.items() if any(len(cells[i]) != 4 for i in items)}
    graph, odd = {-1: []}, {}
    for index, cell in enumerate(cells):
        if len(cell) == 4:
            graph[index] = []
            odd[index] = sum(tuple(sorted(edge)) in split for edge in zip(cell, cell[1:]+cell[:1])) % 2
    for edge, items in owners.items():
        if edge not in split:
            a, b = items if len(items) == 2 else (items[0], -1)
            graph[a].append((b, edge))
            graph[b].append((a, edge))
    visited = set()
    for root in graph:
        if root in visited:
            continue
        stack, order, parents = [root], [], {}
        visited.add(root)
        while stack:
            current = stack.pop()
            order.append(current)
            for other, edge in graph[current]:
                if other not in visited:
                    visited.add(other)
                    parents[other] = current, edge
                    stack.append(other)
        for current in reversed(order[1:]):
            if odd.get(current, 0):
                parent, edge = parents[current]
                split.add(edge)
                odd[current] = 0
                if parent != -1:
                    odd[parent] ^= 1
        if root != -1 and odd[root]:
            raise ValueError("A closed patch transition has inconsistent parity")
    return split


def _single_vertex_fans(faces, tick):
    """Edge-manifoldness alone misses two closed sheets touching at one vertex.

    Union face corners only across their actual shared edges, then require one
    fan per used vertex. Arrays avoid allocating a Python graph per scan vertex.
    """
    size = faces.shape[1]
    half = np.stack((faces, np.roll(faces, -1, axis=1)), axis=2).reshape(-1, 2)
    _edges, inverse, counts = np.unique(np.sort(half, axis=1), axis=0, return_inverse=True, return_counts=True)
    if np.any(counts > 2):
        return False
    order = np.argsort(inverse, kind="stable")
    starts = np.r_[0, np.cumsum(counts[:-1])][counts == 2]
    first, second = order[starts], order[starts+1]
    if np.any(half[first] != half[second, ::-1]):
        return False
    following_first = first//size*size+(first % size+1) % size
    following_second = second//size*size+(second % size+1) % size
    left, right = np.r_[first, following_first], np.r_[following_second, second]
    parent = np.arange(faces.size, dtype=np.int32)
    while True:
        old = parent.copy()
        a, b = parent[left], parent[right]
        np.minimum.at(parent, np.maximum(a, b), np.minimum(a, b))
        while not np.array_equal(parent, parent[parent]):
            parent = parent[parent]
        tick()
        yield
        if np.array_equal(old, parent):
            break
    indices = faces.ravel()
    minimum = np.full(int(indices.max())+1, faces.size, np.int32)
    maximum = np.full_like(minimum, -1)
    np.minimum.at(minimum, indices, parent)
    np.maximum.at(maximum, indices, parent)
    used = np.unique(indices)
    return bool(np.all(minimum[used] == maximum[used]))


class SurfacePatchRemesher:
    """Cooperative source rebuild. Native geometry is only an untouched fallback.

    error is a source-local distance, NOT a requested final face count. The
    source sampling and geometry determine patch density; changing the native
    extraction budget cannot change the rebuilt mesh. Guides are not silently
    ignored: use the separate seed simplifier when guide constraints are needed.
    """

    def __init__(self, vertices, triangles, surface, *, fallback, error,
                 crease_angle=35.0, guides=(), max_seconds=30.0, stitch_seams=True,
                 field_options=None, max_output=MAX_OUTPUT_FACES, planar_only=False):
        self.source = np.array(vertices, np.float64, copy=True), np.array(triangles, np.int32, copy=True)
        self.analysis_source = self.source
        self.stitch_seams, self.source_prepared = bool(stitch_seams), False
        self.field_options = dict(field_options or {})
        self.planar_only = bool(planar_only)
        self.max_output = max(4, int(max_output))
        self.source_verified = self.organic_attempted = False
        self.result = tuple(np.array(values, copy=True) for values in fallback)
        self.surface, self.error = surface, float(error)
        self.guides, self.crease_angle = list(guides), float(crease_angle)
        self.max_seconds = float(max_seconds)
        if not math.isfinite(self.error) or self.error <= 0:
            raise ValueError("Surface detail error must be finite and positive")
        if not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("Surface rebuild time limit must be finite and positive")
        self.phase, self.progress = "Analyzing source surface", 0.0
        self.report = {"method": "SURFACE", "input_faces": len(fallback[1]),
                       "source_triangles": len(triangles), "output_faces": len(fallback[1]),
                       "collapses": 0, "rebuilt": False, "patches": 0,
                       "shape_error_limit": self.error, "sampled_patch_error_max": 0.0,
                       "reason": "Not started"}

    def _organic_status(self, phase, progress):
        self.phase, self.progress = phase, progress

    def _organic(self):
        self.organic_attempted = True
        from .surface_organic import rebuild_field_aligned
        vertices, triangles = self.analysis_source
        output, faces, metrics = yield from rebuild_field_aligned(
            vertices, triangles, error=self.error, max_output=self.max_output,
            tick=self._tick, status=self._organic_status, crease_angle=self.crease_angle,
            guides=self.guides, options=self.field_options)
        self.result = output, faces
        warnings = []
        quality = metrics["output_quality"]
        if len(faces) > self.report["input_faces"]:
            warnings.append("Adaptive retopology is denser than the native comparison; increase Surface Detail Error for coarser output")
        if not metrics["source_triangle_equivalent_reduced"]:
            warnings.append("Quad construction adds triangles relative to the already-coarse source; this is retopology, not a guaranteed triangle reduction")
        if quality["aspect_p95"] > 2 or quality["angle_error_p95_deg"] > 30:
            warnings.append("Some field-aligned quads are stretched; inspect Topology Diagnostics")
        self.report.update(metrics, rebuilt=True, output_faces=len(faces),
            collapses=max(0, self.report["input_faces"]-len(faces)), warnings=warnings,
            reason="Field-aligned quad layout solved at the smallest density meeting the detail limit")

    def _tick(self):
        if time.monotonic()-self.started > self.max_seconds:
            raise ValueError("Time budget reached before a complete conforming rebuild")

    def _relax(self, vertices, faces, references, protected, floor):
        """Free artificial chart junctions, retaining real borders and creases."""
        owners, neighbors = {}, {}
        for index, face in enumerate(faces):
            for corner, vertex in enumerate(face):
                owners.setdefault(int(vertex), []).append(index)
                neighbors.setdefault(int(vertex), set()).update((int(face[corner-1]), int(face[(corner+1) % 4])))
        moved = 0
        for _pass in range(4):
            changes = 0
            for vertex, patch in owners.items():
                if vertex in protected:
                    continue
                old = vertices[faces[patch]]
                normal = face_normals(old)[0].sum(axis=0)
                normal /= max(np.linalg.norm(normal), 1e-30)
                move = (vertices[sorted(neighbors[vertex])].mean(axis=0)-vertices[vertex])*.5
                scale = np.median(np.linalg.norm(vertices[sorted(neighbors[vertex])]-vertices[vertex], axis=1))
                move *= min(1, scale*.3/max(np.linalg.norm(move), 1e-30))
                if np.linalg.norm(move) < scale*1e-5:
                    continue
                hit = self.surface.nearest(vertices[vertex]+move, normal, self.error)
                if hit is None or hit[2] > self.error:
                    continue
                candidate = old.copy()
                candidate[faces[patch] == vertex] = hit[0]
                forward = np.roll(candidate, -1, axis=1)-candidate
                backward = np.roll(candidate, 1, axis=1)-candidate
                normals, areas = face_normals(candidate)
                if (np.any(areas <= floor) or np.any(np.sum(np.cross(forward, backward)*references[patch, None], axis=2) <= floor)
                        or np.any(np.sum(normals*references[patch], axis=1) < math.cos(math.radians(40)))):
                    continue
                if QuadSimplifier._distortion(candidate) < QuadSimplifier._distortion(old)*.9999:
                    vertices[vertex] = hit[0]
                    changes += 1
                self._tick()
                yield
            moved += changes
            if not changes:
                break
        self.report["relaxed_vertices"] = moved

    def run(self):
        self.started = time.monotonic()
        self.fine_faces = set()
        try:
            for _attempt in range(5):
                try:
                    yield from self._run()
                    break
                except _RefinePatch as error:
                    if not self.planar_only and len(self.analysis_source[1]) >= 64 and len(set(error.members)) > 128:
                        yield from self._organic()
                        break
                    newly_fine = set(error.members)-self.fine_faces
                    if not newly_fine:
                        raise ValueError(str(error))
                    self.fine_faces.update(newly_fine)
                    self.report["refined_source_triangles"] = len(self.fine_faces)
                    self.phase = "Retaining finer cells around a difficult patch"
                    self._tick()
                    yield
            else:
                raise ValueError("Patch repair limit reached; no partial rebuild accepted")
        except (ValueError, KeyError) as error:
            if (not self.planar_only and self.source_verified and not self.organic_attempted and len(self.analysis_source[1]) >= 64
                    and time.monotonic()-self.started < self.max_seconds-2):
                try:
                    yield from self._organic()
                except Exception as organic_error:
                    self.report["reason"] = f"Native preview retained: {organic_error}"
                    self.report["failed_phase"] = self.phase
            else:
                self.report["reason"] = f"Native preview retained: {error}"
                self.report["failed_phase"] = self.phase
        self.report["seconds"] = time.monotonic()-self.started

    def _run(self):
        vertices, triangles = self.analysis_source
        if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all() or
                triangles.ndim != 2 or triangles.shape[1] != 3 or not len(triangles) or
                triangles.min() < 0 or triangles.max() >= len(vertices)):
            raise ValueError("Requires finite source triangles")
        if len(triangles) > MAX_SOURCE_FACES:
            raise ValueError(f"Experimental limit: {MAX_SOURCE_FACES:,} source triangles")
        if any(guide.enabled for guide in self.guides):
            # The field-aligned route can carry the same guide strokes into its
            # secondary solve; the planar patch construction cannot.
            try:
                from . import _native  # noqa: F401
            except Exception as error:
                raise ValueError("Guides need the native field solver here; choose Seed Simplify") from error
            yield from self._organic()
            return
        if not self.source_prepared:
            if self.stitch_seams:
                from .surface_topology import stitch_render_seams
                self.phase = "Reconstructing GLB render seams for analysis"
                vertices, triangles, preparation = yield from stitch_render_seams(vertices, triangles, self._tick)
                self.analysis_source = vertices, triangles
                self.report["source_preparation"] = preparation
            else:
                self.report["source_preparation"] = {"enabled": False, "stitched_vertices": 0, "seam_edge_pairs": 0}
            self.source_prepared = True
        preparation = self.report.get("runtime_source_preparation", self.report.get("source_preparation", {}))
        if preparation.get("ambiguous_render_edges"):
            raise ValueError(f"Source has {preparation['ambiguous_render_edges']} ambiguous/non-manifold render seam edges; "
                             "not repaired. Use an explicit geometry-repair workflow before Surface Rebuild")
        source_quality = mesh_quality(vertices, triangles)
        source_boundary = _boundary_components(vertices, triangles)
        if source_quality["degenerate_faces"] or source_quality["nonmanifold_edges"]:
            raise ValueError("Requires a non-degenerate manifold source")
        extent = float(np.linalg.norm(np.ptp(vertices, axis=0)))
        floor = max(extent*extent*1e-13, 1e-30)
        points = vertices[triangles]
        normals = np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0])
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-30)
        half = triangles[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2)
        edges, inverse, counts = np.unique(np.sort(half, axis=1), axis=0, return_inverse=True, return_counts=True)
        order = np.argsort(inverse, kind="stable")
        starts = np.r_[0, np.cumsum(counts[:-1])]
        owners = np.repeat(np.arange(len(triangles)), 3)[order]
        adjacent = np.full((len(edges), 2), -1, np.int32)
        adjacent[:, 0] = owners[starts]
        paired = counts == 2
        adjacent[paired, 1] = owners[starts[paired]+1]
        winding = np.bincount(inverse, weights=np.where(half[:, 0] < half[:, 1], 1, -1))
        if np.any(winding[paired] != 0):
            raise ValueError("Source triangle winding is inconsistent")
        if not (yield from _single_vertex_fans(triangles, self._tick)):
            raise ValueError("Source has a non-manifold vertex fan")
        self.source_verified = True
        neighbors = [[] for _ in triangles]
        blocked = [[] for _ in triangles]
        roughness = np.zeros(len(triangles))
        smooth_pairs = planar_pairs = 0
        for edge in np.flatnonzero(paired):
            a, b = map(int, adjacent[edge])
            dot = float(normals[a] @ normals[b])
            roughness[a] = max(roughness[a], 1-dot)
            roughness[b] = max(roughness[b], 1-dot)
            if self.crease_angle <= 0 or dot >= math.cos(math.radians(self.crease_angle)):
                smooth_pairs += 1
                planar_pairs += dot >= math.cos(math.radians(.05))
                neighbors[a].append(b)
                neighbors[b].append(a)
            else:
                blocked[a].append(b)
                blocked[b].append(a)
            if edge % 256 == 0:
                self._tick()
                yield
        _centre, whole_normal, whole_deviation = _fit_plane(vertices, normals[0])
        whole_planar = (whole_deviation <= self.error*.4 and
                        np.min(normals @ whole_normal) >= math.cos(math.radians(25)))
        self.report["planar_edge_fraction"] = planar_pairs/max(1, smooth_pairs)
        # Organic and hard-surface relief need the field layout. The planar
        # patch experiment is retained for whole-planar sources and for tests
        # that explicitly request it, not as the production organic path.
        if not self.planar_only and not whole_planar and len(triangles) >= 256:
            yield from self._organic()
            return
        self.phase = "Replacing smooth source regions with coarse patches"
        labels = np.full(len(triangles), -1, np.int32)
        patches = []
        examined = assigned = 0
        for seed in np.argsort(roughness, kind="stable"):
            if labels[seed] >= 0:
                continue
            label = len(patches)
            origin, normal = points[seed, 0], normals[seed]
            boundary, used, members = set(), set(), []
            queue = deque([int(seed)])
            while queue:
                face = queue.popleft()
                if labels[face] >= 0:
                    continue
                if members and (face in self.fine_faces or any(labels[other] == label for other in blocked[face])):
                    continue
                examined += 1
                if examined % 64 == 0:
                    self._tick()
                    yield
                values = list(map(int, triangles[face]))
                if (float(normals[face] @ normal) < math.cos(math.radians(25)) or
                        np.max(np.abs((vertices[values]-origin) @ normal)) > self.error*.4):
                    centre, candidate_normal, deviation = _fit_plane(vertices[sorted(used | set(values))], normal)
                    if (deviation > self.error*.4 or
                            np.min(normals[members+[face]] @ candidate_normal) < math.cos(math.radians(25))):
                        continue
                    origin, normal = centre, candidate_normal
                directed = list(zip(values, values[1:]+values[:1]))
                if members:
                    shared = sum(edge[::-1] in boundary for edge in directed)
                    if shared not in (1, 2) or (shared == 1 and len(used & set(values)) != 2):
                        continue
                for edge in directed:
                    if edge[::-1] in boundary:
                        boundary.remove(edge[::-1])
                    else:
                        boundary.add(edge)
                used.update(values)
                members.append(face)
                labels[face] = label
                assigned += 1
                if face not in self.fine_faces:
                    queue.extend(neighbors[face])
            if not members:
                raise ValueError("Could not seed a valid surface patch")
            patches.append((members, _loop(boundary), normal))
            self.progress = .1+.35*assigned/len(triangles)
        # Greedy growth can strand small cells between fronts. Refit whole
        # neighboring regions and dissolve those interfaces, rather than
        # preserving a checkerboard of seed triangles as "detail".
        self.phase = "Dissolving unnecessary patch interfaces"
        working = [{"members": members, "loop": loop, "normal": normal,
                    "vertices": set(map(int, triangles[members].ravel()))}
                   for members, loop, normal in patches]
        nearby = [set() for _ in patches]
        forbidden = [set() for _ in patches]
        for a, b in adjacent[paired]:
            first, second = int(labels[a]), int(labels[b])
            if first != second:
                nearby[first].add(second)
                nearby[second].add(first)
        for face, others in enumerate(blocked):
            first = int(labels[face])
            for other in others:
                second = int(labels[other])
                if first != second:
                    forbidden[first].add(second)
        for _pass in range(4):
            merges = 0
            order = sorted((i for i, item in enumerate(working) if item), key=lambda i: len(working[i]["members"]))
            for first in order:
                if working[first] is None:
                    continue
                a = working[first]
                if self.fine_faces & set(a["members"]):
                    continue
                for second in sorted(nearby[first], key=lambda i: -len(working[i]["members"]) if working[i] else 0):
                    b = working[second]
                    if b is None or second in forbidden[first] or self.fine_faces & set(b["members"]):
                        continue
                    members = a["members"]+b["members"]
                    used = a["vertices"] | b["vertices"]
                    centre, normal, deviation = _fit_plane(vertices[sorted(used)], a["normal"])
                    if (deviation > self.error*.4 or
                            np.min(normals[members] @ normal) < math.cos(math.radians(25))):
                        continue
                    boundary = set(zip(a["loop"], a["loop"][1:]+a["loop"][:1]))
                    for edge in zip(b["loop"], b["loop"][1:]+b["loop"][:1]):
                        if edge[::-1] in boundary:
                            boundary.remove(edge[::-1])
                        else:
                            boundary.add(edge)
                    try:
                        loop = _loop(boundary)
                    except ValueError:
                        continue
                    working[second] = {"members": members, "vertices": used, "loop": loop, "normal": normal}
                    working[first] = None
                    labels[a["members"]] = second
                    updated = (nearby[first] | nearby[second])-{first, second}
                    nearby[second] = updated
                    for neighbor in updated:
                        nearby[neighbor].discard(first)
                        nearby[neighbor].add(second)
                    nearby[first].clear()
                    protected = (forbidden[first] | forbidden[second])-{first, second}
                    forbidden[second] = protected
                    for neighbor in protected:
                        forbidden[neighbor].discard(first)
                        forbidden[neighbor].add(second)
                    forbidden[first].clear()
                    merges += 1
                    break
                self._tick()
                yield
            if not merges:
                break
        remapped = {old: new for new, old in enumerate(i for i, item in enumerate(working) if item)}
        labels = np.array([remapped[int(label)] for label in labels], np.int32)
        patches = [(item["members"], item["loop"], item["normal"]) for item in working if item]
        self.report["patches"] = len(patches)
        self.phase = "Resampling shared patch boundaries"
        interfaces, global_neighbors = {}, {}
        for edge, (a, b) in zip(edges, adjacent):
            first, second = int(labels[a]), int(labels[b]) if b >= 0 else -1
            if first == second:
                continue
            pair = tuple(sorted((first, second)))
            x, y = map(int, edge)
            graph = interfaces.setdefault(pair, {})
            graph.setdefault(x, set()).add(y)
            graph.setdefault(y, set()).add(x)
            global_neighbors.setdefault(x, set()).add(y)
            global_neighbors.setdefault(y, set()).add(x)
        kept = set()
        for index, (pair, graph) in enumerate(interfaces.items()):
            junctions = {v for v in graph if len(graph[v]) != 2 or len(global_neighbors[v]) != 2}
            visited = set()
            for start in sorted(junctions)+sorted(set(graph)-junctions):
                for following in sorted(graph[start]):
                    if tuple(sorted((start, following))) in visited:
                        continue
                    chain, previous, current = [start], start, following
                    while True:
                        visited.add(tuple(sorted((previous, current))))
                        chain.append(current)
                        if current in junctions or current == start:
                            break
                        next_vertex = next(v for v in graph[current] if v != previous)
                        previous, current = current, next_vertex
                    tolerance = self.error*.2
                    first, second = pair
                    protected_interface = (first >= 0 and second >= 0 and
                        any(labels[other] == second for face in patches[first][0] for other in blocked[face]))
                    if -1 not in pair and not protected_interface:
                        # Internal chart boundaries are NOT geometric features.
                        # Keeping their source-grid stair steps would recreate
                        # dense, skinny topology on an otherwise smooth surface.
                        length = float(np.linalg.norm(np.diff(vertices[chain], axis=0), axis=1).sum())
                        tolerance = max(tolerance, length*.25)
                    kept.update(_simplify_chain(vertices, chain, tolerance))
            if index % 32 == 0:
                self._tick()
                yield
        self.report["interface_vertices_before"] = len(global_neighbors)
        self.report["interface_vertices_after"] = len(kept)
        self.phase = "Building new conforming quads"
        output = [point.copy() for point in vertices]
        faces, midpoint_ids, patch_faces, region_cells = [], {}, [], []
        repair_members = []

        def project(point, normal):
            hit = self.surface.nearest(point, normal, self.error)
            if hit is None or hit[2] > self.error:
                raise ValueError("A new patch vertex exceeds the source error limit")
            # Fit the coarse proxy, not every fine displacement. Snapping each
            # midpoint back to detail can fold a very thin cell and reintroduce
            # the detail this rebuild is meant to leave out of the topology.
            return point

        coordinates = np.empty((len(vertices), 2))
        for index, (members, loop, normal) in enumerate(patches):
            self._tick()
            polygon = [v for v in loop if v in kept]
            if len(polygon) < 3:
                repair_members.extend(members)
                continue
            axes = [axis for axis in range(3) if axis != int(np.abs(normal).argmax())]
            if np.cross(np.eye(3)[axes[0]], np.eye(3)[axes[1]]) @ normal < 0:
                axes.reverse()
            coordinates[polygon] = vertices[polygon][:, axes]
            def valid_cell(cell):
                corners = vertices[cell]
                mids = (corners+np.roll(corners, -1, axis=0))*.5
                centre = corners.mean(axis=0)
                quads = np.stack((corners, mids, np.tile(centre, (len(cell), 1)), np.roll(mids, 1, axis=0)), axis=1)
                forward = np.roll(quads, -1, axis=1)-quads
                backward = np.roll(quads, 1, axis=1)-quads
                measured, areas = face_normals(quads)
                return (np.all(areas > floor) and np.all(np.sum(np.cross(forward, backward)*normal, axis=2) > floor)
                        and np.all(measured @ normal >= math.cos(math.radians(40))))

            try:
                cells = _convex_cells(polygon, coordinates, floor, valid_cell)
            except ValueError:
                repair_members.extend(members)
                continue
            region_cells.append(cells)
            yield
        if repair_members:
            raise _RefinePatch(repair_members, "Some patch interfaces require finer conforming cells")
        self.phase = "Joining coarse and fine patches without T-junctions"
        split_edges = _transition_edges([cell for cells in region_cells for cell in cells])
        self.report["transition_edges"] = len(split_edges)
        for index, ((members, _loop_values, normal), cells) in enumerate(zip(patches, region_cells)):
            first = len(faces)
            for cell in cells:
                split = [tuple(sorted((a, b))) in split_edges for a, b in zip(cell, cell[1:]+cell[:1])]
                if len(cell) == 4 and not any(split):
                    faces.append(cell)
                    continue
                mids = {}
                boundary = []
                for corner, (a, b) in enumerate(zip(cell, cell[1:]+cell[:1])):
                    boundary.append(a)
                    if not split[corner]:
                        continue
                    edge = tuple(sorted((a, b)))
                    if edge not in midpoint_ids:
                        midpoint_ids[edge] = len(output)
                        output.append(project((vertices[a]+vertices[b])*.5, normal))
                    mids[corner] = midpoint_ids[edge]
                    boundary.append(midpoint_ids[edge])
                if len(cell) == 4 and sum(split) == 2 and split[0] == split[2]:
                    # Opposite-edge split: two ordinary quads, no central pole.
                    offset = 0 if split[0] else 1
                    a, b, c, d = cell[offset:]+cell[:offset]
                    m, n = mids[offset], mids[(offset+2) % 4]
                    faces.extend(([a, m, n, d], [m, b, c, n]))
                    continue
                centre = len(output)
                output.append(project(vertices[cell].mean(axis=0), normal))
                chosen = None
                for offset in (0, 1):
                    loop = boundary[offset:]+boundary[:offset]
                    candidate = [[loop[i], loop[(i+1) % len(loop)], loop[(i+2) % len(loop)], centre]
                                 for i in range(0, len(loop), 2)]
                    points = np.asarray([[output[vertex] for vertex in face] for face in candidate])
                    forward = np.roll(points, -1, axis=1)-points
                    backward = np.roll(points, 1, axis=1)-points
                    if np.all(np.sum(np.cross(forward, backward)*normal, axis=2) > floor):
                        chosen = candidate
                        break
                if chosen is None:
                    raise _RefinePatch(members, "A coarse/fine transition would create a degenerate corner")
                faces.extend(chosen)
            patch_faces.append((first, len(faces)))
            if len(faces) > MAX_OUTPUT_FACES:
                raise ValueError(f"Experimental limit: {MAX_OUTPUT_FACES:,} rebuilt quads")
            self.progress = .45+.25*(index+1)/len(patches)
            self._tick()
            yield
        self.report["candidate_faces"] = len(faces)
        if len(faces)*2 > len(triangles):
            raise ValueError("Patch repair would increase source density, not coarsen it; increase Surface Detail Error or use Seed Simplify")
        output, faces = np.asarray(output), np.asarray(faces, np.int32)
        references = np.zeros((len(faces), 3))
        for (_members, _loop_values, normal), (first, last) in zip(patches, patch_faces):
            references[first:last] = normal
        output_edges_values, output_counts, _sizes = output_edges(faces)
        protected = set(map(int, output_edges_values[output_counts == 1].ravel()))
        crease_vertices = {int(vertex) for face, others in enumerate(blocked) if others for vertex in triangles[face]}
        protected.update(crease_vertices & kept)
        for edge, midpoint in midpoint_ids.items():
            if all(vertex in crease_vertices for vertex in edge):
                protected.add(midpoint)
        self.phase = "Relaxing the rebuilt topology, not the native grid"
        self.report["pre_relax_quality"] = mesh_quality(output, faces)
        yield from self._relax(output, faces, references, protected, floor)
        self.phase = "Checking source detail and new patch surfaces"
        maximum = 0.0
        for index, ((members, _loop_values, normal), (first, last)) in enumerate(zip(patches, patch_faces)):
            self._tick()
            quads = output[faces[first:last]]
            measured_normals, areas = face_normals(quads)
            forward, backward = np.roll(quads, -1, axis=1)-quads, np.roll(quads, 1, axis=1)-quads
            corners = np.sum(np.cross(forward, backward)*normal, axis=2)
            if (np.any(areas <= floor) or np.any(corners <= floor) or
                    np.any(measured_normals @ normal < math.cos(math.radians(40)))):
                raise _RefinePatch(members, f"Patch {index+1} has a folded/degenerate quad")
            samples = np.einsum("sj,fjk->fsk", SAMPLE_WEIGHTS, quads).reshape(-1, 3)
            for sample_id, (point, expected) in enumerate(zip(samples, np.repeat(measured_normals, len(SAMPLE_WEIGHTS), axis=0))):
                hit = self.surface.nearest(point, expected, self.error)
                if hit is None or hit[2] > self.error or np.dot(hit[1], expected) < .5:
                    raise _RefinePatch(members, "A rebuilt quad exceeds the sampled source error limit")
                maximum = max(maximum, float(hit[2]))
                if sample_id % 128 == 0:
                    self._tick()
                    yield
            self.progress = .7+.1*(index+1)/len(patches)
        # Internal interfaces may move across original source faces. Check
        # against the WHOLE conforming result, not an obsolete per-patch face
        # assignment, which would wrongly prohibit that topology redistribution.
        self.phase = "Checking all source witnesses against the rebuilt surface"
        witnesses = np.vstack((vertices[np.unique(triangles)], vertices[triangles].mean(axis=1),
                               vertices[edges].mean(axis=1)))
        quads = output[faces]
        trees = _output_trees(quads)
        for begin in range(0, len(witnesses), 512):
            maximum = max(maximum, _reverse_error(witnesses[begin:begin+512], quads, trees))
            if maximum > self.error:
                raise ValueError("A coarse patch loses source detail beyond the sampled error limit")
            self.progress = .8+.2*min(1, (begin+512)/len(witnesses))
            self._tick()
            yield
        used, inverse = np.unique(faces, return_inverse=True)
        result = output[used].astype(np.float32), inverse.reshape(-1, 4).astype(np.int32)
        quality = mesh_quality(*result)
        self.report["candidate_quality"] = quality
        self.report["candidate_sampled_error_max"] = maximum
        if quality["angle_error_p95_deg"] > 80:
            raise ValueError("Rebuilt transitions contain near-degenerate angles; native preview is safer")
        if (quality["components"] != source_quality["components"] or
                quality["euler_characteristic"] != source_quality["euler_characteristic"] or
                quality["nonmanifold_edges"] or quality["degenerate_faces"] or
                _boundary_components(*result) != source_boundary):
            raise ValueError("The complete rebuild failed the source topology guard")
        if not (yield from _single_vertex_fans(result[1], self._tick)):
            raise ValueError("The complete rebuild has a non-manifold vertex fan")
        self.result = result
        warnings = []
        if len(result[1]) > self.report["input_faces"]:
            warnings.append("Source rebuild is denser than the native comparison; it is not a face-budget reducer")
        if quality["aspect_p95"] > 3 or quality["angle_error_p95_deg"] > 45:
            warnings.append("Aggressive patch transitions contain stretched/skewed quads; inspect Topology Diagnostics")
        self.report.update(rebuilt=True, output_faces=len(result[1]),
                           collapses=max(0, self.report["input_faces"]-len(result[1])),
                           sampled_patch_error_max=maximum, output_quality=quality, warnings=warnings,
                           reason="Source-driven patch rebuild complete; native grid discarded")
