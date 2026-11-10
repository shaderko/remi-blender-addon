"""Cached Blender 5.3 native 3D renders for file-backed board cards.

No custom splat shader, subsampling, ellipsoid proxies, or foreground imports.
Live previews reuse an isolated native scene and drawing space during orbit;
one-shot captures release their scene immediately.
"""

import math
from pathlib import Path
import struct
import uuid
import zlib

import numpy as np


def camera_frame(arrays, *, yaw=0.0, elevation=0.0):
    """Frame the Gaussian bounds, including their anisotropic extent."""
    positions = np.asarray(arrays["positions"], dtype=np.float32)
    scales = np.asarray(arrays["scales"], dtype=np.float32)
    visible = np.asarray(arrays["opacities"]) >= 1 / 255
    if not np.any(visible):
        return np.zeros(3), np.array((0.0, -1.0, 0.0))
    lower, upper = np.percentile(positions[visible], (0.2, 99.8), axis=0)
    centre = (lower + upper) * 0.5
    radius = max(float(np.linalg.norm(upper - lower) * 0.5), 0.01)
    radius += float(np.percentile(scales[visible].max(axis=1), 90)) * 3
    yaw, elevation = math.radians(yaw), math.radians(elevation)
    outward = np.array((math.sin(yaw)*math.cos(elevation),
                        -math.cos(yaw)*math.cos(elevation), math.sin(elevation)))
    distance = radius / math.sin(math.radians(20)) * 1.08
    return centre, centre + outward * distance


def preview_screen():
    """Copy a drawing space, never toggle the customer's viewport shading."""
    import bpy

    current = bpy.context.screen
    screens = ([current] if current else []) + [screen for screen in bpy.data.screens if screen != current]
    for screen in screens:
        if screen.get("remi_preview_screen"):
            continue
        if any(area.type == "VIEW_3D" for area in screen.areas):
            copied = screen.copy()
            copied.name = "Remi · isolated preview screen"
            copied["remi_preview_screen"] = True
            return copied
    raise RuntimeError("Native 3D previews need a 3D Viewport in the Remi workspace")


class NativePreview:
    def __init__(self, arrays, *, size=512, yaw=0.0, elevation=0.0):
        import bpy
        import gpu
        from mathutils import Vector

        self.offscreen = None
        self.scene = self.screen = self.obj = self.camera = self.camera_data = None
        self._subject_ids = set()
        self.size = size
        try:
            self.screen = preview_screen()
            area = next(area for area in self.screen.areas if area.type == "VIEW_3D")
            self.space = area.spaces.active
            self.region = next(region for region in area.regions if region.type == "WINDOW")
            self.space.shading.type = "MATERIAL"
            self.space.shading.use_scene_lights = False
            self.space.shading.use_scene_world = False
            self.space.overlay.show_overlays = False
            self.space.show_gizmo = False
            self.space.show_object_viewport_pointcloud = True
            self.scene = bpy.data.scenes.new("Remi · isolated native preview")
            self.scene.render.engine = "BLENDER_EEVEE"
            self.scene.render.film_transparent = True
            self.scene.view_settings.view_transform = "Standard"
            centre, location = self._create_subject(arrays)
            self.centre = Vector(centre)
            self.distance = (Vector(location)-self.centre).length
            self.camera_data = bpy.data.cameras.new("Remi · temporary preview camera")
            self.camera = bpy.data.objects.new("Remi · temporary preview camera", self.camera_data)
            self.scene.collection.objects.link(self.camera)
            self.camera_data.lens = 36 / (2 * math.tan(math.radians(20)))
            self.camera_data.clip_start = max(0.0001, self.distance/10000)
            self.camera_data.clip_end = max(1000, self.distance*10)
            self.scene.camera = self.camera
            self.offscreen = gpu.types.GPUOffScreen(size, size, format="RGBA16F")
            self.render(yaw=yaw, elevation=elevation)
        except Exception:
            self.free()
            raise

    def _create_subject(self, arrays):
        from ...blender.gaussian_splats import create_native_splat, native_splats_available

        if not native_splats_available():
            raise RuntimeError("Remi requires Blender 5.3 with native Gaussian rendering")
        self.obj = create_native_splat(Path("remi-board-preview/splats.npz"), arrays, self.scene.collection)
        return camera_frame(arrays)

    def render(self, *, yaw=0.0, elevation=0.0):
        import bpy
        import gpu
        from mathutils import Vector

        yaw, elevation = math.radians(yaw), math.radians(elevation)
        outward = Vector((math.sin(yaw)*math.cos(elevation),
                          -math.cos(yaw)*math.cos(elevation), math.sin(elevation)))
        self.camera.location = self.centre + outward*self.distance
        self.camera.rotation_euler = (self.centre-self.camera.location).to_track_quat("-Z", "Y").to_euler()
        layer = self.scene.view_layers[0]
        viewport_state, blend = gpu.state.viewport_get(), gpu.state.blend_get()
        try:
            with bpy.context.temp_override(scene=self.scene, view_layer=layer):
                layer.update()
                projection = self.camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(), x=self.size, y=self.size)
                self.offscreen.draw_view3d(self.scene, layer, self.space, self.region,
                    self.camera.matrix_world.inverted(), projection,
                    do_color_management=False, draw_background=False)
        finally:
            gpu.state.viewport_set(*viewport_state)
            gpu.state.blend_set(blend)

    def _release_subject(self):
        import bpy

        if self.obj is not None:
            cloud = self.obj.data
            bpy.data.objects.remove(self.obj, do_unlink=True)
            bpy.data.pointclouds.remove(cloud)
            self.obj = None

    def release_scene(self):
        import bpy

        self._release_subject()
        if self.camera is not None:
            bpy.data.objects.remove(self.camera, do_unlink=True)
            self.camera = None
        if self.camera_data is not None:
            bpy.data.cameras.remove(self.camera_data)
            self.camera_data = None
        if self.scene is not None:
            bpy.data.scenes.remove(self.scene)
            self.scene = None
        if self.screen is not None:
            # Screens have no collection.remove API; remove only our copy.
            bpy.data.batch_remove(ids=[self.screen])
            self.screen = None

    @property
    def texture(self):
        return self.offscreen.texture_color

    def save(self, path):
        """Capture the native render as a portable, display-correct thumbnail."""
        import gpu

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.stem + "." + uuid.uuid4().hex + ".png")
        try:
            width, height = self.texture.width, self.texture.height
            with self.offscreen.bind():
                buffer = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, "FLOAT")
                pixels = np.array(buffer, dtype=np.float32)
            # Explicit PNG encoding avoids Blender's float-image save path
            # unpremultiplying alpha in display space (bright translucent rims).
            alpha = pixels[:, :, 3:4]
            rgb = np.divide(pixels[:, :, :3], alpha, out=np.zeros_like(pixels[:, :, :3]), where=alpha > 1e-6)
            rgb = np.clip(rgb, 0, 1)
            rgb = np.where(rgb <= 0.0031308, rgb*12.92, 1.055*rgb**(1/2.4)-0.055)
            rgba = np.rint(np.clip(np.concatenate((rgb, alpha), axis=2), 0, 1)*255).astype(np.uint8)
            rows = b"".join(b"\0" + row.tobytes() for row in rgba[::-1])
            def chunk(kind, value):
                return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind+value))
            temporary.write_bytes(b"\x89PNG\r\n\x1a\n" +
                chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
                chunk(b"sRGB", b"\0") + chunk(b"IDAT", zlib.compress(rows, level=3)) + chunk(b"IEND", b""))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def free(self):
        if self.offscreen is not None:
            self.offscreen.free()
            self.offscreen = None
        self.release_scene()


class Preview(NativePreview):
    """One-shot native capture: only the rendered texture survives creation."""

    def __init__(self, arrays, **options):
        super().__init__(arrays, **options)
        self.release_scene()


class NativeMeshPreview(NativePreview):
    """The same reusable viewport for PLY and textured, multipart GLB meshes.

    Imports belong only to the disposable preview scene. Track their exact IDs
    at creation so freeing one preview cannot remove another preview/user edit.
    """

    def _create_subject(self, path):
        import bpy
        from mathutils import Vector
        from .editing import import_working_mesh

        path = Path(path)
        if not path.is_file():
            raise ValueError("The mesh preview source file is missing")
        names = ("objects", "meshes", "materials", "images", "node_groups", "textures",
                 "collections", "armatures", "actions", "shape_keys", "cameras", "lights")
        before = {name: set(getattr(bpy.data, name)) for name in names}
        layer = self.scene.view_layers[0]
        try:
            with bpy.context.temp_override(scene=self.scene, view_layer=layer, collection=self.scene.collection):
                meshes = import_working_mesh(bpy.context, path)
                layer.update()
                depsgraph = bpy.context.evaluated_depsgraph_get()
                points = [obj.matrix_world @ Vector(corner) for mesh in meshes
                          for obj in (mesh.evaluated_get(depsgraph),) for corner in obj.bound_box]
            lower = Vector([min(point[axis] for point in points) for axis in range(3)])
            upper = Vector([max(point[axis] for point in points) for axis in range(3)])
            centre = (lower+upper)*0.5
            radius = max((upper-lower).length*0.5, 0.01)
            distance = radius/math.sin(math.radians(20))*1.12
            self.obj = meshes[0]
            return centre, centre+Vector((0, -distance, 0))
        finally:
            self._subject_ids = {item for name in names for item in set(getattr(bpy.data, name))-before[name]}

    def _release_subject(self):
        import bpy

        if self._subject_ids:
            bpy.data.batch_remove(ids=list(self._subject_ids))
            self._subject_ids.clear()
        self.obj = None
