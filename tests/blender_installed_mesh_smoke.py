"""Check Create Mesh from the installed 5.3 extension (normal user profile)."""
import argparse
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
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("archive", type=Path)
parser.add_argument("--quality", choices=("DENSE", "FAST"), default="DENSE")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
archive = args.archive.resolve()
output = Path(tempfile.mkdtemp(prefix="remi-installed-mesh-", dir=bpy.app.tempdir))
settings = bpy.context.scene.remi_generation
assert settings.mesh_quality == "DENSE"
settings.mesh_quality = args.quality
settings.mesh_output = str(output)
client = importlib.import_module(module_name + ".integrations.splat_mesh.client")
adapter = importlib.import_module(module_name + ".blender.reconstructed_mesh")
splats = importlib.import_module(module_name + ".blender.gaussian_splats")
splat = splats.import_splats(bpy.context, archive, display="NATIVE")
snapshot = output / "selected_object.npz"
splats.write_splat_archive(splat, snapshot)
job = client.MeshJob.start(
    python=bpy.path.abspath(settings.python), archive=snapshot, output=output, quality=settings.mesh_quality,
)
print("INSTALLED MESH JOB", job.directory, flush=True)
try:
    deadline = time.monotonic() + 600
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
assert not (Path(addon.__file__).parent / "scripts").exists()
import json
report = json.loads((job.directory / "report.json").read_text())
assert report["quality"] == args.quality and report["schema"] == 2
assert (job.directory / "source.npz").is_file()
bpy.context.scene.remi_flow.preset = "RECONSTRUCTED"
assert bpy.context.scene.remi_flow.preset == "RECONSTRUCTED"
print("INSTALLED MESH OK", len(obj.data.polygons), obj.data.color_attributes[0].name, flush=True)