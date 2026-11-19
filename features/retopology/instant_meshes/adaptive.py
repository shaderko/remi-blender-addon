"""Experimental, sampled-error-bounded quad collapse. Never touches Blender IDs.

A quad is removed by merging *opposite* vertices, not by triangulating and
running triangle decimation. Link, winding, valence, distortion and protected
feature checks are deliberately conservative. Historical patch witnesses are
retained through every collapse, so the error budget cannot drift per step.
This is not a proof against global self-intersection or deformation artifacts.
"""

from collections import Counter
import heapq
import math
import time

import numpy as np


MAX_FACES = 25000
# Corners/edges and both possible triangulations. Blender can choose either
# diagonal when displaying/exporting a non-planar quad.
SAMPLE_WEIGHTS = np.array([
    [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1],
    [.5, .5, 0, 0], [0, .5, .5, 0], [0, 0, .5, .5], [.5, 0, 0, .5],
    [1/3, 1/3, 1/3, 0], [1/3, 0, 1/3, 1/3], [.5, 0, .5, 0],
    [1/3, 1/3, 0, 1/3], [0, 1/3, 1/3, 1/3], [0, .5, 0, .5],
], dtype=np.float64)


def face_normals(points):
    vectors = (np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0]) +
               np.cross(points[:, 2]-points[:, 0], points[:, 3]-points[:, 0]))
    lengths = np.linalg.norm(vectors, axis=1)
    return vectors/np.maximum(lengths[:, None], 1e-30), lengths*.5


def triangle_distances(points, triangles):
    """Squared distances to each triangle; no centroid/vertex-only shortcut."""
    a = triangles[None, :, 0]
    ab, ac = triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]
    delta = points[:, None]-a
    aa, bb, cc = np.sum(ab*ab, axis=1), np.sum(ab*ac, axis=1), np.sum(ac*ac, axis=1)
    d, e = np.sum(delta*ab, axis=2), np.sum(delta*ac, axis=2)
    denominator = np.maximum(aa*cc-bb*bb, 1e-30)
    u, v = (d*cc-e*bb)/denominator, (e*aa-d*bb)/denominator
    normal = np.cross(ab, ac)
    plane = np.sum(delta*normal, axis=2)**2/np.maximum(np.sum(normal*normal, axis=1), 1e-30)
    result = np.full(plane.shape, np.inf)
    inside = (u >= 0) & (v >= 0) & (u+v <= 1)
    result[inside] = plane[inside]
    for start, end in ((0, 1), (1, 2), (2, 0)):
        origin = triangles[:, start]
        edge = triangles[:, end]-origin
        local = points[:, None]-origin
        t = np.clip(np.sum(local*edge, axis=2)/np.maximum(np.sum(edge*edge, axis=1), 1e-30), 0, 1)
        result = np.minimum(result, np.sum((local-t[:, :, None]*edge)**2, axis=2))
    return result


class BVHSurface:
    """Read-only, orientation-aware projection onto the original source."""

    def __init__(self, tree):
        self.tree = tree

    def nearest(self, point, normal, limit):
        from mathutils import Vector

        point = Vector(point)
        hit = self.tree.find_nearest(point)
        if hit[0] is not None and np.dot(hit[1], normal) >= .5:
            return np.array(hit[0]), np.array(hit[1]), hit[3]
        matches = [item for item in self.tree.find_nearest_range(point, limit)
                   if item[0] is not None and np.dot(item[1], normal) >= .5]
        if not matches:
            return None
        hit = min(matches, key=lambda item: item[3])
        return np.array(hit[0]), np.array(hit[1]), hit[3]


class QuadSimplifier:
    """Cooperative preview task: advance run() on the main thread in small slices.

    error is in the source's local coordinate system, not a per-collapse amount.
    Boundaries, resolved sharp edges and all enabled guide tubes are protected.
    Reaching the requested budget is never more important than those guards.
    """

    def __init__(self, vertices, faces, surface, *, target_faces, error,
                 crease_angle=35.0, guides=(), max_seconds=30.0):
        self.original = (np.array(vertices, dtype=np.float32, copy=True),
                         np.array(faces, dtype=np.int32, copy=True))
        self.surface, self.guides = surface, list(guides)
        self.target = max(4, int(target_faces))
        self.error, self.crease_angle = float(error), float(crease_angle)
        if not math.isfinite(self.error) or self.error <= 0:
            raise ValueError("Adaptive shape error must be finite and positive")
        if not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError("Adaptive time limit must be finite and positive")
        self.max_seconds = max_seconds
        self.result = self.original
        self.report = {"input_faces": len(faces), "output_faces": len(faces),
                       "target_faces": self.target, "collapses": 0, "reached_target": False,
                       "shape_error_limit": self.error, "sampled_patch_error_max": 0.0,
                       "protected_vertices": 0, "rejected": {}, "reason": "Not started"}
        self.progress, self.phase = 0.0, "Preparing adaptive quads"
        self.rejected = Counter()

    def _neighbors(self, vertex):
        result = set()
        for face in self.vfaces[vertex]:
            values = self.f[face]
            index = int(np.flatnonzero(values == vertex)[0])
            result.update((int(values[(index-1) % 4]), int(values[(index+1) % 4])))
        return result

    def _edges(self, values):
        return [tuple(sorted((int(a), int(b)))) for a, b in zip(values, np.roll(values, -1))]

    def _priority(self, face, diagonal):
        values = np.roll(self.f[face], -diagonal)
        a, b, c, d = map(int, values)
        if self.locked[values].any():
            return None
        na, nc = self._neighbors(a), self._neighbors(c)
        if c in na or na & nc != {b, d}:
            return None
        degrees = [len(na), len(self._neighbors(b)), len(nc), len(self._neighbors(d))]
        after = [degrees[0]+degrees[2]-2, degrees[1]-1, degrees[3]-1]
        if min(after) < 3 or max(after) > 6:
            return None
        patch = sorted(self.vfaces[a] | self.vfaces[c])
        point = (self.v[a]+self.v[c])*.5
        normals, _ = face_normals(self.v[self.f[patch]])
        height = np.sum((point-self.v[self.f[patch, 0]])*normals, axis=1)
        # Smooth patches win; the valence term steers transitions away from
        # repeatedly creating high-degree poles. It is not a semantic loop score.
        valence = sum((n-4)**2 for n in after)-sum((n-4)**2 for n in degrees)
        cost = float(np.mean(height*height)/self.error**2 + .03*valence)
        cost += .01*float(np.linalg.norm(self.v[a]-self.v[c])**2/self.edge_scale**2)
        return cost

    def _push(self, face):
        if not self.alive[face]:
            return
        for diagonal in (0, 1):
            cost = self._priority(face, diagonal)
            if cost is not None:
                signature = tuple(self.versions[self.f[face]])
                heapq.heappush(self.heap, (cost, face, diagonal, signature))

    def _guide_locks(self):
        if not self.guides:
            return
        from mathutils.kdtree import KDTree
        tree = KDTree(len(self.v))
        for index, point in enumerate(self.v):
            tree.insert(point, index)
        tree.balance()
        tube = self.edge_scale*.4
        segments = 0
        for guide in self.guides:
            if not guide.enabled:
                continue
            points = np.asarray(guide.positions)
            pairs = list(zip(points[:-1], points[1:]))
            if guide.closed and len(points) > 2:
                pairs.append((points[-1], points[0]))
            for a, b in pairs:
                segments += 1
                if segments > 100000:
                    raise ValueError("Guide protection exceeds the experimental analysis limit")
                edge = b-a
                length = float(np.linalg.norm(edge))
                for _point, index, _distance in tree.find_range((a+b)*.5, length*.5+tube):
                    t = np.clip(np.dot(self.v[index]-a, edge)/max(length*length, 1e-30), 0, 1)
                    if np.linalg.norm(self.v[index]-a-t*edge) <= tube:
                        self.locked[index] = True
                if segments % 32 == 0:
                    yield

    def _witnesses(self, face):
        if face not in self.witnesses:
            points = SAMPLE_WEIGHTS @ self.original[0][self.original[1][face]]
            # Sample the source under the original patch, not just the native
            # preview's vertices. Keep those same witnesses through later edits.
            for index, point in enumerate(points):
                hit = self.surface.nearest(point, self.reference_normals[face], self.error)
                if hit is not None and hit[2] <= self.error:
                    points[index] = hit[0]
            self.witnesses[face] = points
        return self.witnesses[face]

    def _candidate(self, face, diagonal):
        a, b, c, d = map(int, np.roll(self.f[face], -diagonal))
        patch = sorted(self.vfaces[a] | self.vfaces[c])
        remaining = [index for index in patch if index != face]
        normal = self.reference_normals[patch].sum(axis=0)
        normal /= max(np.linalg.norm(normal), 1e-30)
        hit = self.surface.nearest((self.v[a]+self.v[c])*.5, normal, self.error)
        if hit is None or hit[2] > self.error:
            return None, "projection"
        location = hit[0]
        replacement = self.f[remaining].copy()
        replacement[replacement == c] = a
        if any(len(set(values)) != 4 for values in replacement):
            return None, "topology"
        keys = [tuple(sorted(values)) for values in replacement]
        if len(set(keys)) != len(keys) or any(self.face_keys.get(key, face) not in patch for key in keys):
            return None, "duplicate faces"
        points = self.v[replacement].copy()
        points[replacement == a] = location
        checked, reason = self._check_patch(points, patch, remaining)
        if checked is None:
            return None, reason
        witnesses, closest, maximum = checked
        return (a, c, location, patch, remaining, replacement, witnesses, closest, maximum), ""

    def _check_patch(self, points, patch, remaining):
        normals, areas = face_normals(points)
        reference = self.reference_normals[remaining]
        if np.any(areas < self.area_floor) or np.any(np.sum(normals*reference, axis=1) < math.cos(math.radians(35))):
            return None, "folding"
        edges = np.roll(points, -1, axis=1)-points
        backwards = np.roll(points, 1, axis=1)-points
        lengths = np.linalg.norm(edges, axis=2)
        if np.any(lengths.max(axis=1)/np.maximum(lengths.min(axis=1), 1e-30) > self.aspect_limits[remaining]):
            return None, "stretching"
        # Positive convex corners and non-flipped diagonal triangles reject
        # bow-ties/concavity; arbitrary Blender n-gon triangulation is not used.
        cross = np.cross(edges, backwards)
        corner = np.sum(cross*reference[:, None], axis=2)
        if np.any(corner <= self.area_floor):
            return None, "folding"
        cosines = np.sum(edges*backwards, axis=2)/(lengths*np.maximum(np.linalg.norm(backwards, axis=2), 1e-30))
        if np.any(np.abs(cosines) > math.cos(math.radians(25))):
            return None, "angles"
        triangles = points[:, [[0, 1, 2], [0, 2, 3], [0, 1, 3], [1, 2, 3]]].reshape(-1, 3, 3)
        tri_normal = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        if np.any(np.sum(tri_normal*np.repeat(reference, 4, axis=0), axis=1) <= self.area_floor):
            return None, "folding"
        samples = np.einsum("sj,fjk->fsk", SAMPLE_WEIGHTS, points).reshape(-1, 3)
        maximum = 0.0
        for point, expected in zip(samples, np.repeat(normals, len(SAMPLE_WEIGHTS), axis=0)):
            nearest = self.surface.nearest(point, expected, self.error)
            if nearest is None or nearest[2] > self.error or np.dot(nearest[1], expected) < math.cos(math.radians(45)):
                return None, "source error"
            maximum = max(maximum, float(nearest[2]))
        witnesses = np.concatenate([self._witnesses(index) for index in patch])
        if not len(witnesses):
            return (witnesses, np.empty(0, np.int32), maximum), ""
        distances = triangle_distances(witnesses, triangles).reshape(len(witnesses), len(remaining), 2, 2)
        distances = distances.min(axis=3).max(axis=2)
        closest = distances.argmin(axis=1)
        reverse = distances[np.arange(len(witnesses)), closest]
        if reverse.max() > self.error**2:
            return None, "historical patch error"
        maximum = max(maximum, float(np.sqrt(reverse.max())))
        return (witnesses, closest, maximum), ""

    def _apply(self, candidate):
        a, c, location, patch, remaining, replacement, witnesses, closest, maximum = candidate
        touched = set(self.f[patch].ravel())
        for index in patch:
            values = self.f[index]
            self.face_keys.pop(tuple(sorted(values)), None)
            for vertex in values:
                self.vfaces[vertex].remove(index)
            self.witnesses.pop(index, None)
            self.alive[index] = False
        self.v[a] = location
        for offset, index in enumerate(remaining):
            values = replacement[offset]
            self.f[index] = values
            self.alive[index] = True
            self.face_keys[tuple(sorted(values))] = index
            for vertex in values:
                self.vfaces[vertex].add(index)
            self.witnesses[index] = witnesses[closest == offset]
        self.versions[list(touched)] += 1
        affected = set().union(*(self.vfaces[v] for v in touched))
        for index in sorted(affected):
            self._push(index)
        self.report["collapses"] += 1
        self.report["sampled_patch_error_max"] = max(self.report["sampled_patch_error_max"], maximum)

    @staticmethod
    def _distortion(points):
        forward, backward = np.roll(points, -1, axis=1)-points, np.roll(points, 1, axis=1)-points
        lengths = np.linalg.norm(forward, axis=2)
        cosines = np.sum(forward*backward, axis=2)/(lengths*np.maximum(np.linalg.norm(backward, axis=2), 1e-30))
        aspect = lengths.max(axis=1)/np.maximum(lengths.min(axis=1), 1e-30)
        return float(np.mean(cosines*cosines)+.1*np.mean((aspect-1)**2))

    def _relax_vertex(self, vertex):
        if self.locked[vertex] or not self.vfaces[vertex]:
            return
        patch = sorted(self.vfaces[vertex])
        old = self.v[self.f[patch]]
        target = self.v[list(self._neighbors(vertex))].mean(axis=0)
        move = (target-self.v[vertex])*.5
        length = np.linalg.norm(move)
        if length < self.edge_scale*1e-6:
            return
        move *= min(1, self.edge_scale*.3/length)
        normal = self.reference_normals[patch].sum(axis=0)
        normal /= max(np.linalg.norm(normal), 1e-30)
        hit = self.surface.nearest(self.v[vertex]+move, normal, self.error)
        if hit is None or hit[2] > self.error:
            return
        points = old.copy()
        points[self.f[patch] == vertex] = hit[0]
        if self._distortion(points) >= self._distortion(old)*.9999:
            return
        checked, _reason = self._check_patch(points, patch, patch)
        if checked is not None:
            witnesses, closest, maximum = checked
            self.v[vertex] = hit[0]
            for offset, face in enumerate(patch):
                self.witnesses[face] = witnesses[closest == offset]
            self.report["relaxed_vertices"] += 1
            self.report["sampled_patch_error_max"] = max(self.report["sampled_patch_error_max"], maximum)

    def _relax(self, started):
        """Improve quad angles tangentially, under the *same historical* guards."""
        self.phase = "Relaxing quad shape within the error limit"
        self.report["relaxed_vertices"] = 0
        for _pass in range(3):
            for vertex in range(len(self.v)):
                if time.monotonic()-started > self.max_seconds:
                    return
                self._relax_vertex(vertex)
                yield

    def run(self):
        started = time.monotonic()
        vertices, faces = self.original
        if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all()
                or faces.ndim != 2 or faces.shape[1] != 4 or not len(faces)
                or faces.min() < 0 or faces.max() >= len(vertices)):
            self.report["reason"] = "Requires a finite all-quad preview"
            return
        if len(faces) > MAX_FACES:
            self.report["reason"] = f"Experimental limit: {MAX_FACES:,} preview faces"
            return
        if self.target >= len(faces):
            self.report.update(reason="Full native budget", reached_target=True)
            return
        from .quality import mesh_quality

        quality = mesh_quality(vertices, faces)
        if quality["quad_fraction"] != 1 or quality["degenerate_faces"] or quality["nonmanifold_edges"]:
            self.report["reason"] = "Ordinary preview retained: requires non-degenerate manifold quads"
            return
        self.v, self.f = vertices.astype(np.float64), faces.copy()
        self.reference_normals, areas = face_normals(self.v[self.f])
        self.area_floor = max(float(np.linalg.norm(np.ptp(self.v, axis=0))**2)*1e-12, 1e-30)
        self.edge_scale = max(quality["median_edge_length"], 1e-15)
        lengths = np.linalg.norm(np.roll(self.v[self.f], -1, axis=1)-self.v[self.f], axis=2)
        self.aspect_limits = np.maximum(3.0, lengths.max(axis=1)/np.maximum(lengths.min(axis=1), 1e-30)*1.05)
        self.locked = np.zeros(len(vertices), bool)
        self.alive = np.ones(len(faces), bool)
        self.versions = np.zeros(len(vertices), np.int64)
        self.vfaces = [set() for _ in vertices]
        self.face_keys, incidence = {}, {}
        for index, values in enumerate(self.f):
            key = tuple(sorted(values))
            if key in self.face_keys:
                self.report["reason"] = "Ordinary preview retained: duplicate input faces"
                return
            self.face_keys[key] = index
            for vertex in values:
                self.vfaces[vertex].add(index)
            for edge in self._edges(values):
                incidence.setdefault(edge, []).append(index)
            if index % 128 == 0:
                yield
        for edge, owners in incidence.items():
            sharp = (len(owners) == 2 and self.crease_angle > 0 and
                     np.dot(self.reference_normals[owners[0]], self.reference_normals[owners[1]]) <
                     math.cos(math.radians(self.crease_angle)))
            if len(owners) != 2 or sharp:
                self.locked[list(edge)] = True
        # Edge-manifold alone is insufficient: reject bow-tie vertices too.
        for vertex, owners in enumerate(self.vfaces):
            if not owners:
                continue
            link = {}
            for face in owners:
                values = self.f[face]
                corner = int(np.flatnonzero(values == vertex)[0])
                a, b = int(values[(corner-1) % 4]), int(values[(corner+1) % 4])
                link.setdefault(a, set()).add(b)
                link.setdefault(b, set()).add(a)
            visited, stack = set(), [next(iter(link))]
            while stack:
                current = stack.pop()
                if current not in visited:
                    visited.add(current)
                    stack.extend(link[current]-visited)
            degrees = [len(items) for items in link.values()]
            if len(visited) != len(link) or max(degrees) > 2 or degrees.count(1) not in (0, 2):
                self.report["reason"] = "Ordinary preview retained: non-manifold vertex fan"
                return
            if vertex % 128 == 0:
                yield
        self.phase = "Protecting guide paths"
        yield from self._guide_locks()
        self.report["protected_vertices"] = int(self.locked.sum())
        self.heap, self.witnesses = [], {}
        self.phase = "Ranking smooth quad patches"
        for index in range(len(faces)):
            self._push(index)
            if index % 32 == 0:
                yield
        self.phase = "Simplifying quads"
        needed = len(faces)-self.target
        attempts = 0
        reason = "Shape, feature, topology or quality limits reached"
        while self.heap and self.report["collapses"] < needed:
            if time.monotonic()-started > self.max_seconds:
                reason = "Time budget reached; keeping the completed valid reductions"
                break
            if attempts >= len(faces)*12:
                reason = "Candidate budget reached; keeping the completed valid reductions"
                break
            _cost, face, diagonal, signature = heapq.heappop(self.heap)
            if not self.alive[face] or signature != tuple(self.versions[self.f[face]]):
                yield
                continue
            attempts += 1
            if self._priority(face, diagonal) is None:
                continue
            candidate, rejection = self._candidate(face, diagonal)
            if candidate is not None:
                self._apply(candidate)
            else:
                self.rejected[rejection] += 1
            self.progress = self.report["collapses"]/max(1, needed)
            yield
            if len(self.heap) > len(faces)*10:
                self.heap.clear()
                for index in np.flatnonzero(self.alive):
                    self._push(int(index))
                    if index % 32 == 0:
                        yield
        if self.report["collapses"]:
            yield from self._relax(started)
        output_faces = self.f[self.alive]
        used, inverse = np.unique(output_faces, return_inverse=True)
        result = self.v[used].astype(np.float32), inverse.reshape(-1, 4).astype(np.int32)
        measured = mesh_quality(*result)
        if (measured["components"] != quality["components"] or
                measured["euler_characteristic"] != quality["euler_characteristic"] or
                measured["boundary_edges"] != quality["boundary_edges"] or
                measured["nonmanifold_edges"] or measured["degenerate_faces"]):
            self.report.update(reason="Ordinary preview retained: final topology guard failed", collapses=0)
            return
        self.result = result
        self.report.update(output_faces=len(result[1]), reached_target=len(result[1]) <= self.target,
                           reason="Requested budget reached" if len(result[1]) <= self.target else reason,
                           rejected=dict(self.rejected), seconds=time.monotonic()-started,
                           input_quality=quality, output_quality=measured)
