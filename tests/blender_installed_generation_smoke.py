"""Check saved enablement, local defaults and generation from an installed copy.

Run Blender normally in --background mode (NOT --factory-startup), with
--python-exit-code 1 --python this_file -- --image <image>, or --archive <npz>.
This does not modify startup.blend or save the temporary test scene.
"""
import argparse
import importlib
import json
from pathlib import Path
import sys

import bpy


parser = argparse.ArgumentParser(description=__doc__)
source = parser.add_mutually_exclusive_group(required=True)
source.add_argument("--image", type=Path)
source.add_argument("--archive", type=Path)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
module_name = "bl_ext.user_default.remi"
assert module_name in bpy.context.preferences.addons, "Remi is not enabled in saved preferences"
assert module_name in sys.modules, "Remi did not load automatically on startup"
addon = importlib.import_module(module_name)
repository = next(repo for repo in bpy.context.preferences.extensions.repos if repo.module == "user_default")
assert Path(addon.__file__).resolve().is_relative_to(Path(repository.directory).resolve() / "remi")
assert bpy.ops.remi.generate_splats.poll()
assert hasattr(bpy.types, "Remi_PT_GenerationSettings")
assert hasattr(bpy.context.window_manager, "remi_ui")
adapter = importlib.import_module(module_name + ".blender.gaussian_splats")
client = importlib.import_module(module_name + ".integrations.triposplat.client")
assert adapter.native_splats_available()
settings = bpy.context.scene.remi_generation
assert Path(bpy.path.abspath(settings.python)).is_file(), settings.python
models = Path(bpy.path.abspath(settings.models))
for name in client.COMPONENTS:
    assert (models / name).is_file(), name
assert settings.output
print(f"Installed module: {addon.__file__}", flush=True)
print(f"Configured Python: {settings.python}", flush=True)
print(f"Configured model bundle: {settings.models}", flush=True)

if args.image:
    job = client.GenerationJob.start(
        python=bpy.path.abspath(settings.python), models=models, image=args.image.resolve(),
        output=bpy.path.abspath(settings.output), gaussians=65536, seed=20261023,
    )
    print(f"Installed worker output: {job.directory}", flush=True)
    try:
        job.process.wait(timeout=240)
        archive = job.result()
    finally:
        job.cancel()
else:
    archive = args.archive.resolve()

assert bpy.ops.remi.import_splats(filepath=str(archive)) == {"FINISHED"}
obj = bpy.context.active_object
assert adapter.uses_native_splats(obj)
assert len(obj.data.points) == obj["remi_splat_count"]
assert not obj.modifiers and not obj.data.materials
assert not bpy.ops.remi.start_session.poll()
assert "torch" not in sys.modules
report = {
    "blender": bpy.app.version_string, "module": addon.__file__,
    "enabled_on_startup": True, "models": str(models), "python": settings.python,
    "archive": str(archive), "gaussians": len(obj.data.points),
    "native_pointcloud": obj.data.type,
}
if args.image:
    (archive.parent / "installed_addon_smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2), flush=True)
print("REMI_INSTALLED_GENERATION_SMOKE_OK", flush=True)
