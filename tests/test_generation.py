"""Gaussian exchange and worker-boundary tests; no Blender or torch required."""
import importlib.util
import hashlib
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "remi_test_triposplat"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "integrations" / "triposplat")]
sys.modules[PACKAGE] = package


def module(name):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", Path(package.__path__[0]) / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result


data = module("splat_data")
client = module("client")
worker = sys.modules[f"{PACKAGE}.worker"]


def example(count=3):
    return {
        "positions": np.arange(count * 3, dtype=np.float32).reshape(count, 3),
        "scales": np.full((count, 3), 0.1, dtype=np.float32),
        "rotations": np.tile(np.array([1, 0, 0, 0], dtype=np.float32), (count, 1)),
        "opacities": np.linspace(0, 1, count, dtype=np.float32),
        "sh_dc": np.full((count, 3), -2.5, dtype=np.float32),
    }


class SplatDataTests(unittest.TestCase):
    def test_roundtrip_preserves_all_native_values(self):
        original = example()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "splats.npz"
            data.save(path, original)
            loaded = data.load(path)
            for name in original:
                np.testing.assert_array_equal(loaded[name], original[name])

    def test_invalid_arrays_rejected(self):
        for name, value in (("positions", np.full((3, 3), np.nan)),
                            ("scales", np.zeros((3, 3))),
                            ("rotations", np.zeros((3, 4))),
                            ("opacities", np.array([-1, 0, 1])),
                            ("sh_dc", np.zeros((3, 4)))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                data.validate({**example(), name: value})

    def test_schema_and_pickle_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "splats.npz"
            np.savez(path, schema=2, **example())
            with self.assertRaisesRegex(ValueError, "schema"):
                data.load(path)
            np.savez(path, schema=1, **{**example(), "positions": np.array([object()])})
            with self.assertRaises(ValueError):
                data.load(path)


class WorkerTests(unittest.TestCase):
    def test_status_retries_windows_reader_lock_without_failing_generation(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(Path, "replace", side_effect=[PermissionError(), None]) as replace, \
                 mock.patch.object(worker.time, "sleep"):
                self.assertTrue(worker.status(Path(directory), "Encoding"))
                self.assertEqual(replace.call_count, 2)
            with mock.patch.object(Path, "replace", side_effect=PermissionError()), \
                 mock.patch.object(worker.time, "sleep"):
                self.assertFalse(worker.status(Path(directory), "Encoding"))

    def test_snapshot_sources_match_recorded_hashes(self):
        vendor = Path(package.__path__[0]) / "vendor"
        provenance = json.loads((vendor / "provenance.json").read_text(encoding="utf-8"))
        for name, metadata in provenance["files"].items():
            with self.subTest(name=name), (vendor / name).open("rb") as stream:
                self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), metadata["sha256"])

    def test_spawn_uses_isolated_python_and_file_protocol(self):
        with tempfile.TemporaryDirectory(prefix="remi path with spaces ") as directory:
            path = Path(directory)
            image = path / "source image.png"
            image.touch()
            for name in client.COMPONENTS:
                component = path / name
                component.parent.mkdir(parents=True, exist_ok=True)
                component.touch()
            process = mock.Mock()
            process.poll.return_value = None
            with mock.patch.object(client.subprocess, "Popen", return_value=process) as spawn:
                job = client.GenerationJob.start(python=sys.executable, models=path, image=image,
                                                 output=path, gaussians=65536, seed=123)
            try:
                command = spawn.call_args.args[0]
                self.assertEqual(command[1:4], ["-I", "-B", "-u"])
                self.assertNotIn("shell", spawn.call_args.kwargs)
                request = json.loads((job.directory / "request.json").read_text())
                self.assertEqual(request["image"], str(image.resolve()))
                self.assertEqual(request["seed"], 123)
                with self.assertRaisesRegex(RuntimeError, "not finished"):
                    job.result()
                (job.directory / "status.json").write_text("{")
                self.assertEqual(job.status()["phase"], "Starting Python worker")
                (job.directory / "complete.json").write_text('{"schema": 1, "complete": true}')
                (job.directory / "splats.npz").touch()
                process.poll.return_value = 0
                self.assertEqual(job.result(), job.directory / "splats.npz")
            finally:
                job.cancel()

    def test_missing_components_fail_before_process_spawn(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(client.subprocess, "Popen") as spawn:
            path = Path(directory)
            image = path / "image.png"
            image.touch()
            with self.assertRaisesRegex(ValueError, "Missing model files"):
                client.GenerationJob.start(python=sys.executable, image=image, models=path,
                                           output=path, gaussians=65536, seed=0)
            spawn.assert_not_called()

    def test_cancel_reaps_real_child_and_keeps_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            log = (path / "worker.log").open("wb")
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            job = client.GenerationJob(child, path, log)
            self.assertIsNone(job.poll())
            job.cancel()
            self.assertIsNotNone(child.poll())
            self.assertTrue(log.closed)
            self.assertTrue((path / "worker.log").is_file())

    def test_worker_failure_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            request = path / "request.json"
            request.write_text(json.dumps({"models": str(path), "gaussians": 65536, "seed": 0}))
            result = subprocess.run([sys.executable, "-I", "-B", str(client.Path(client.__file__).with_name("worker.py")),
                                     "--request", str(request)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing model component", json.loads((path / "status.json").read_text())["error"])

    def test_importing_client_does_not_load_torch(self):
        result = subprocess.run([sys.executable, "-I", "-c",
            f"import sys; sys.path.insert(0, {str(ROOT / 'integrations')!r}); "
            "from triposplat.client import GenerationJob; assert 'torch' not in sys.modules"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
