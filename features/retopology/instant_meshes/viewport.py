import bpy
import numpy as np

from .runtime import runtime


_draw_handle = None
_source_cache_key = None
_source_batch = None
_preview_cache_key = None
_preview_fill_batch = None
_preview_line_batch = None


def _world_points(points, matrix):
    if points is None or len(points) == 0:
        return np.empty((0, 3), dtype=np.float32)
    transform = np.asarray(matrix, dtype=np.float32)
    return points @ transform[:3, :3].T + transform[:3, 3]


def _world_vectors(vectors, matrix, normal=False):
    transform = np.asarray(matrix, dtype=np.float32)[:3, :3]
    if normal:
        transform = np.linalg.inv(transform).T
    result = vectors @ transform.T
    lengths = np.linalg.norm(result, axis=1)
    valid = lengths > 1e-12
    result[valid] /= lengths[valid, None]
    return result


def _draw_lines(coordinates, color, width=1.0):
    if coordinates is None or len(coordinates) < 2:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": coordinates})
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_line_strip(coordinates, color, width=2.0):
    if coordinates is None or len(coordinates) < 2:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": coordinates})
    gpu.state.line_width_set(width)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_points(coordinates, color, size=5.0):
    if coordinates is None or len(coordinates) == 0:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
    batch = batch_for_shader(shader, "POINTS", {"pos": coordinates})
    shader.bind()
    shader.uniform_float("color", color)
    shader.uniform_float("size", size)
    batch.draw(shader)


def _reset_caches():
    global _source_cache_key, _source_batch
    global _preview_cache_key, _preview_fill_batch, _preview_line_batch
    _source_cache_key = None
    _source_batch = None
    _preview_cache_key = None
    _preview_fill_batch = None
    _preview_line_batch = None


def _draw_local_batch(batch, shader, matrix, color):
    if batch is None:
        return
    import gpu

    gpu.matrix.push()
    try:
        gpu.matrix.multiply_matrix(matrix)
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
    finally:
        gpu.matrix.pop()


def _get_source_batch(shader):
    global _source_cache_key, _source_batch
    if runtime.surface_vertices is None or runtime.surface_faces is None:
        return None
    key = (id(runtime.session), id(runtime.surface_vertices), id(runtime.surface_faces))
    if key == _source_cache_key:
        return _source_batch

    from gpu_extras.batch import batch_for_shader

    _source_batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": runtime.surface_vertices},
        indices=runtime.surface_faces,
    )
    _source_cache_key = key
    return _source_batch


def _get_preview_batches(shader, offset_factor):
    global _preview_cache_key, _preview_fill_batch, _preview_line_batch
    if runtime.preview is None or runtime.preview_normals is None:
        return None, None
    vertices, faces = runtime.preview
    key = (
        id(runtime.session),
        id(vertices),
        id(faces),
        id(runtime.preview_normals),
        float(offset_factor),
    )
    if key == _preview_cache_key:
        return _preview_fill_batch, _preview_line_batch

    from gpu_extras.batch import batch_for_shader

    offset_vertices = vertices + runtime.preview_normals * (
        runtime.target_scale * float(offset_factor)
    )
    if faces.shape[1] == 4:
        quads = faces[:, 2] != faces[:, 3]
        triangles = [faces[:, :3]]
        if np.any(quads):
            triangles.append(faces[quads][:, (0, 2, 3)])
        triangles = np.concatenate(triangles, axis=0)
        face_sizes = np.where(quads, 4, 3)
    else:
        triangles = faces[:, :3]
        face_sizes = np.full(len(faces), 3, dtype=np.int32)

    edge_chunks = []
    for corner in range(faces.shape[1]):
        valid = face_sizes > corner
        if not np.any(valid):
            continue
        next_corner = (corner + 1) % face_sizes[valid]
        edge_chunks.append(
            np.stack((faces[valid, corner], faces[valid, next_corner]), axis=1)
        )
    edges = np.concatenate(edge_chunks, axis=0)
    edges.sort(axis=1)
    edges = np.unique(edges, axis=0)
    segments = offset_vertices[edges.reshape(-1)]

    _preview_fill_batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": offset_vertices},
        indices=triangles,
    )
    _preview_line_batch = batch_for_shader(
        shader,
        "LINES",
        {"pos": segments},
    )
    _preview_cache_key = key
    return _preview_fill_batch, _preview_line_batch


def draw_overlay():
    if not runtime.ready:
        return
    import gpu

    source = runtime.source
    settings = runtime.settings()
    if not source or not settings:
        return
    matrix = source.matrix_world
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    try:
        uniform_shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        # RetopoFlow-style focus treatment: preserve the source depth buffer so
        # back-side topology remains hidden, then darken only its visible surface.
        if settings.source_dimming > 0.0:
            _draw_local_batch(
                _get_source_batch(uniform_shader),
                uniform_shader,
                matrix,
                (0.01, 0.015, 0.025, float(settings.source_dimming)),
            )

        if settings.show_orientation and runtime.orientation is not None:
            positions, normals, directions = runtime.orientation
            p = _world_points(positions, matrix)
            n = _world_vectors(normals, matrix, normal=True)
            q = _world_vectors(directions, matrix)
            t = np.cross(n, q)
            t_length = np.linalg.norm(t, axis=1)
            valid = t_length > 1e-12
            t[valid] /= t_length[valid, None]
            object_scale = max(abs(value) for value in matrix.to_scale())
            size = max(float(runtime.target_scale) * object_scale * 0.34, 1e-6)
            offset = size * 0.15
            centers = p + n * offset
            q_lines = np.stack((centers - q * size, centers + q * size), axis=1).reshape(-1, 3)
            t_lines = np.stack((centers - t * size, centers + t * size), axis=1).reshape(-1, 3)
            _draw_lines(q_lines, (0.15, 0.75, 1.0, 0.8), 1.0)
            _draw_lines(t_lines, (0.9, 0.35, 1.0, 0.65), 1.0)

        if settings.show_position and runtime.position is not None:
            positions, normals = runtime.position
            p = _world_points(positions, matrix)
            n = _world_vectors(normals, matrix, normal=True)
            _draw_points(p + n * 0.001, (0.3, 1.0, 0.45, 0.75), 3.0)

        for stroke in runtime.strokes:
            color = (1.0, 0.55, 0.1, 1.0) if stroke.stroke_type == 1 else (0.1, 0.9, 1.0, 1.0)
            p = _world_points(stroke.positions, matrix)
            n = _world_vectors(stroke.normals, matrix, normal=True)
            object_scale = max(abs(value) for value in matrix.to_scale())
            offset = max(
                runtime.target_scale
                * object_scale
                * max(0.06, float(settings.preview_offset)),
                1e-6,
            )
            _draw_line_strip(p + n * offset, color, 4.0)

        if settings.show_singularities:
            if runtime.orientation_singularities is not None:
                _draw_points(
                    _world_points(runtime.orientation_singularities[0], matrix),
                    (1.0, 0.15, 0.15, 1.0),
                    8.0,
                )
            if runtime.position_singularities is not None:
                _draw_points(
                    _world_points(runtime.position_singularities[0], matrix),
                    (1.0, 0.8, 0.05, 1.0),
                    7.0,
                )

        if settings.show_preview and runtime.preview is not None:
            fill_batch, line_batch = _get_preview_batches(
                uniform_shader, settings.preview_offset
            )
            gpu.state.depth_test_set("NONE" if settings.preview_xray else "LESS_EQUAL")
            if settings.preview_fill_opacity > 0.0:
                _draw_local_batch(
                    fill_batch,
                    uniform_shader,
                    matrix,
                    (0.04, 0.32, 0.22, float(settings.preview_fill_opacity)),
                )
            gpu.state.line_width_set(2.25)
            _draw_local_batch(
                line_batch,
                uniform_shader,
                matrix,
                (0.15, 1.0, 0.58, 1.0),
            )
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.depth_mask_set(True)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")


def register_overlay():
    global _draw_handle
    if _draw_handle is None:
        _reset_caches()
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            draw_overlay, (), "WINDOW", "POST_VIEW"
        )


def unregister_overlay():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    _reset_caches()
