"""Installed 5.3 end-to-end: Create Mesh → Reconstructed · Decimate."""
import importlib
from pathlib import Path
import sys
import tempfile
import time

import bpy

module_name = "bl_ext.user_default.remi"
assert module_name in sys.modules
addon = importlib.import_module(module_name)
archive = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
output = Path(tempfile.mkdtemp(prefix="remi-installed-mesh-opt-", dir=bpy.app.tempdir))
settings = bpy.context.scene.remi_generation
settings.mesh_quality = "FAST"
settings.mesh_output = str(output)
client = importlib.import_module(module_name + ".integrations.splat_mesh.client")
adapter = importlib.import_module(module_name + ".blender.reconstructed_mesh")
splats = importlib.import_module(module_name + ".blender.gaussian_splats")
splat = splats.import_splats(bpy.context, archive, display="NATIVE")
job = client.MeshJob.start(
    python=bpy.path.abspath(settings.python), archive=archive, output=output,
    blender=bpy.app.binary_path, tool_folder=bpy.path.abspath(settings.surface_tools), quality=settings.mesh_quality,
)
try:
    deadline = time.monotonic() + 600
    while job.poll() is None and time.monotonic() < deadline:
        time.sleep(1)
    mesh = job.result()
finally:
    job.cancel()
obj = adapter.import_reconstructed_mesh(bpy.context, mesh, source_object=splat,
                                        report=job.directory / "report.json")
before = len(obj.data.polygons)
bpy.context.scene.remi_flow.preset = "RECONSTRUCTED"
assert bpy.ops.remi.run_full_flow() == {"FINISHED"}
after = len(bpy.context.active_object.data.polygons)
assert 0 < after < before, (before, after)
assert len(bpy.context.active_object.data.color_attributes) >= 1
assert len(bpy.context.active_object.data.materials) >= 1
print("INSTALLED MESH OPTIMIZE OK", before, "->", after, flush=True)
