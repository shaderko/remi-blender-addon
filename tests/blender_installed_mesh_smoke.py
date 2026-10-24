"""Check Create Mesh from the installed 5.3 extension (normal user profile)."""
import importlib
from pathlib import Path
import sys
import tempfile
import time

import bpy

module_name = "bl_ext.user_default.remi"
assert module_name in bpy.context.preferences.addons
assert module_name in sys.modules
addon = importlib.import_module(module_name)
archive = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
output = Path(tempfile.mkdtemp(prefix="remi-installed-mesh-", dir=bpy.app.tempdir))
settings = bpy.context.scene.remi_generation
settings.mesh_detail = "160"
settings.mesh_smoothing = "6"
settings.mesh_output = str(output)
client = importlib.import_module(module_name + ".integrations.splat_mesh.client")
adapter = importlib.import_module(module_name + ".blender.reconstructed_mesh")
splats = importlib.import_module(module_name + ".blender.gaussian_splats")
splat = splats.import_splats(bpy.context, archive, display="NATIVE")
job = client.MeshJob.start(
    python=bpy.path.abspath(settings.python), archive=archive, output=output,
    resolution=160, taubin=6, min_component=500,
)
print("INSTALLED MESH JOB", job.directory, flush=True)
try:
    deadline = time.monotonic() + 180
    while job.poll() is None and time.monotonic() < deadline:
        time.sleep(1)
    mesh = job.result()
finally:
    job.cancel()
obj = adapter.import_reconstructed_mesh(bpy.context, mesh, source_object=splat,
                                        report=job.directory / "report.json")
assert adapter.is_reconstructed_mesh(obj)
assert obj.data.polygons and len(obj.data.color_attributes) >= 1
assert len(obj.data.materials) == 1
bpy.context.scene.remi_flow.preset = "RECONSTRUCTED"
assert bpy.context.scene.remi_flow.preset == "RECONSTRUCTED"
print("INSTALLED MESH OK", len(obj.data.polygons), obj.data.color_attributes[0].name, flush=True)
