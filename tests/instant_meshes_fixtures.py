"""Analytic mesh fixtures shared by detector tests and the Blender benchmark."""

import numpy as np


def ring_surface(rings, segments=64, closed=False):
    vertices = np.asarray(rings, dtype=np.float32).reshape(-1, 3)
    faces = []
    for row in range(len(rings) if closed else len(rings) - 1):
        next_row = (row + 1) % len(rings)
        for column in range(segments):
            following = (column + 1) % segments
            a, b = row * segments + column, row * segments + following
            c, d = next_row * segments + column, next_row * segments + following
            faces.extend(((a, b, d), (a, d, c)))
    return vertices, np.asarray(faces, dtype=np.int32)


def cylinder(segments=64, levels=9, capped=True):
    theta = np.arange(segments) * (2 * np.pi / segments)
    rings = [np.column_stack((np.cos(theta), np.sin(theta), np.full(segments, z)))
             for z in np.linspace(-1.5, 1.5, levels)]
    vertices, faces = ring_surface(rings, segments)
    if capped:
        first = len(vertices)
        vertices = np.vstack((vertices, [[0, 0, -1.5], [0, 0, 1.5]])).astype(np.float32)
        caps = []
        for column in range(segments):
            following = (column + 1) % segments
            caps.extend(((first, following, column),
                         (first + 1, (levels - 1) * segments + column, (levels - 1) * segments + following)))
        faces = np.vstack((faces, caps)).astype(np.int32)
    return vertices, faces


def washer(segments=64, levels=9):
    theta = np.arange(segments) * (2 * np.pi / segments)
    return ring_surface([np.column_stack((r * np.cos(theta), r * np.sin(theta), np.zeros(segments)))
                         for r in np.linspace(0.5, 1.5, levels)], segments)


def torus(segments=64, levels=24):
    theta = np.arange(segments) * (2 * np.pi / segments)
    rings = []
    for phi in np.arange(levels) * (2 * np.pi / levels):
        radius = 1 + 0.3 * np.cos(phi)
        rings.append(np.column_stack((radius * np.cos(theta), radius * np.sin(theta),
                                       np.full(segments, 0.3 * np.sin(phi)))))
    vertices, faces = ring_surface(rings, segments, closed=True)
    return vertices, faces[:, ::-1].copy()


def sphere(segments=64, levels=31):
    theta = np.arange(segments) * (2 * np.pi / segments)
    rings = [np.column_stack((np.sin(phi) * np.cos(theta), np.sin(phi) * np.sin(theta),
                              np.full(segments, np.cos(phi))))
             for phi in np.linspace(0, np.pi, levels + 2)[1:-1]]
    vertices, faces = ring_surface(rings, segments)
    first = len(vertices)
    vertices = np.vstack((vertices, [[0, 0, 1], [0, 0, -1]])).astype(np.float32)
    caps = []
    for column in range(segments):
        following = (column + 1) % segments
        caps.extend(((first, following, column),
                     (first + 1, (levels - 1) * segments + column, (levels - 1) * segments + following)))
    return vertices, np.vstack((faces, caps)).astype(np.int32)


def plane(count=25):
    coordinates = np.linspace(-2, 2, count)
    vertices = np.asarray([(x, y, 0) for y in coordinates for x in coordinates], np.float32)
    faces = []
    for row in range(count - 1):
        for column in range(count - 1):
            a = row * count + column
            faces.extend(((a, a + 1, a + count + 1), (a, a + count + 1, a + count)))
    return vertices, np.asarray(faces, np.int32)
