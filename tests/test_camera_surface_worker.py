"""Packaged reconstruction protocol, exchange, installation, and cancellation tests."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "integrations/splat_mesh"))
from integrations.splat_mesh.client import MeshJob
from remi_surface import pipeline, tool
from remi_surface.common import sha256
from remi_surface.exchange import export_colmap, load_gaussians, write_gaussian_ply
from remi_surface.geometry import archive_bounds, make_rig
from remi_surface.optimizer import (mesh_arguments, optimizer_environment, parse_camera_probe,
                                   train_arguments, validate_camera_probe)
from remi_surface.processes import terminate_tree


def arrays():
    return {"positions": np.array([[0, 0, 0], [1, 2, 3]], np.float32),
            "scales": np.array([[0.1, 0.2, 0.3], [0.2, 0.3, 0.4]], np.float32),
            "rotations": np.array([[1, 0, 0, 0], [1, 0, 0, 0]], np.float32),
            "opacities": np.array([0.2, 1], np.float32), "sh_dc": np.array([[1, 2, 3], [0, 0, 0]], np.float32)}


class ExchangeTests(unittest.TestCase):
    def test_snapshot_and_standard_ply_roundtrip_without_source_ply(self):
        with tempfile.TemporaryDirectory(prefix="remi-surface-exchange-") as directory:
            root = Path(directory)
            source = root / "source.npz"
            np.savez(source, schema=np.array(1), **arrays())
            loaded = load_gaussians(source)
            ply = write_gaussian_ply(root / "source.ply", loaded)
            data = ply.read_bytes()
            header, binary = data.split(b"end_header\n", 1)
            names = [line.split()[-1].decode() for line in header.splitlines() if line.startswith(b"property")]
            records = np.frombuffer(binary, dtype=[(name, "<f4") for name in names])
            np.testing.assert_array_equal(np.column_stack([records[name] for name in ("x", "y", "z")]), loaded["positions"])
            np.testing.assert_allclose(np.exp(np.column_stack([records[f"scale_{i}"] for i in range(3)])), loaded["scales"], rtol=1e-6)
            np.testing.assert_allclose(1 / (1 + np.exp(-records["opacity"])), loaded["opacities"], atol=2e-7)
            np.testing.assert_array_equal(np.column_stack([records[f"rot_{i}"] for i in range(4)]), loaded["rotations"])
            with self.assertRaises(FileExistsError):
                write_gaussian_ply(ply, loaded)

    def test_invalid_gaussians_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="remi-surface-invalid-") as directory:
            source = Path(directory) / "source.npz"
            for key, value in (("scales", np.zeros((2, 3))), ("opacities", np.array([-1, 1])),
                               ("positions", np.full((2, 3), np.nan)), ("rotations", np.zeros((2, 4)))):
                np.savez(source, schema=np.array(1), **{**arrays(), key: value})
                with self.assertRaises(ValueError):
                    load_gaussians(source)

    def test_surface_profiles_preserve_quality_without_silent_cleanup(self):
        train = train_arguments("tool", "data", "source.ply", "out", "fit")
        flags = dict(zip(train[3::2], train[4::2]))
        self.assertEqual(flags["--num-iterations"], "8000")
        self.assertEqual(flags["--cap-max"], "1000000")
        self.assertEqual(flags["--disable-viewer"], "1")
        self.assertEqual(flags["--auto-scale-poses"], "0")
        mesh = mesh_arguments("tool", "checkpoint", "data", "out")
        flags = dict(zip(mesh[3::2], mesh[4::2]))
        for key in ("--merge-factor", "--floater-min-faces", "--fill-hole-ratio", "--fill-hole-max-edges", "--quality-iters"):
            self.assertEqual(flags[key], "0")
        self.assertEqual(flags["--bisection-iters"], "6")

    def test_debug_environment_cannot_accidentally_skip_training(self):
        base = {"SS_DUMP_CAMERAS": "stale.json", "PATH": "preserve"}
        child = optimizer_environment(base)
        self.assertNotIn("SS_DUMP_CAMERAS", child)
        self.assertIn("SS_DUMP_CAMERAS", base)
        self.assertEqual(child["DISABLE_VULKAN_OW_OBS_CAPTURE"], "1")


class ToolTests(unittest.TestCase):
    def test_installer_checks_archive_and_executable_before_publishing(self):
        payload, executable = io.BytesIO(), b"fixture, never executed"
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("spirula.exe", executable)
        blob = payload.getvalue()
        with tempfile.TemporaryDirectory(prefix="remi-tool-install-") as directory:
            opener = mock.Mock()
            opener.open.return_value = io.BytesIO(blob)
            with mock.patch.object(tool, "platform_error", return_value=""), \
                    mock.patch.object(tool, "ARCHIVE_SIZE", len(blob)), \
                    mock.patch.object(tool, "ARCHIVE_SHA256", hashlib.sha256(blob).hexdigest()), \
                    mock.patch.object(tool, "EXECUTABLE_SHA256", hashlib.sha256(executable).hexdigest()), \
                    mock.patch.object(tool.urllib.request, "build_opener", return_value=opener):
                installed = tool.install_tool(directory)
                self.assertEqual(installed.read_bytes(), executable)
                opener.open.reset_mock()
                self.assertEqual(tool.install_tool(directory), installed)
                opener.open.assert_not_called()
                installed.write_bytes(b"modified")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    tool.verify_tool(directory)

    def test_failed_checksum_never_publishes_an_executable(self):
        with tempfile.TemporaryDirectory(prefix="remi-tool-invalid-") as directory:
            opener = mock.Mock()
            opener.open.return_value = io.BytesIO(b"wrong archive")
            with mock.patch.object(tool, "platform_error", return_value=""), \
                    mock.patch.object(tool.urllib.request, "build_opener", return_value=opener):
                with self.assertRaisesRegex(ValueError, "verification"):
                    tool.install_tool(directory)
            self.assertFalse(tool.executable_path(directory).exists())


class CalibrationTests(unittest.TestCase):
    def test_packaged_export_reads_only_training_views_and_keeps_camera_frame(self):
        from PIL import Image
        from scipy.spatial.transform import Rotation
        with tempfile.TemporaryDirectory(prefix="remi-packaged-cameras-") as directory:
            root = Path(directory)
            data = arrays()
            dataset = make_rig(archive_bounds(data), views=3, evaluation_views=1, resolution=64)
            dataset["source_sha256"] = "fixture"
            for pair in dataset["pairs"]:
                destination = root / pair["left"]
                destination.parent.mkdir(parents=True)
                Image.new("RGBA", (64, 64), (200, 100, 50, 128)).save(destination)
            # Right and held-out images deliberately do not exist.
            output = export_colmap(root, root / "calibrated", dataset, data)
            self.assertEqual(len(list((output / "images").glob("*.png"))), 3)
            lines = (output / "sparse/0/images.txt").read_text().splitlines()
            lines = [line.split() for line in lines if line and not line.startswith("#")]
            for line, pair in zip(lines, dataset["pairs"]):
                q = np.array(line[1:5], dtype=float)
                rotation = Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()
                actual = np.eye(4)
                actual[:3, :3], actual[:3, 3] = rotation, np.array(line[5:8], dtype=float)
                np.testing.assert_allclose(actual, np.linalg.inv(pair["left_camera_to_world_opencv"]), atol=1e-9)
            self.assertEqual(json.loads((output / "export.json").read_text())["held_out_views_used"], 0)

    def test_packaged_camera_probe_rejects_coordinate_and_intrinsic_changes(self):
        dataset = make_rig(archive_bounds(arrays()), views=3, evaluation_views=1, resolution=64)
        k = np.asarray(dataset["intrinsics"])
        probe = {"viewmats": np.asarray([np.linalg.inv(pair["left_camera_to_world_opencv"])
                                        for pair in dataset["pairs"]]).ravel().tolist(),
                 "intrins": [k[0, 0], k[1, 1], k[0, 2], k[1, 2]] * 3,
                 "widths": [64] * 3, "heights": [64] * 3, "train_frame_scale": 1.0}
        validate_camera_probe(parse_camera_probe(json.dumps(probe)), dataset)
        for field, value in (("train_frame_scale", 2.0), ("widths", [32] * 3),
                             ("intrins", [1] * 12), ("viewmats", [0] * 48)):
            with self.assertRaises(RuntimeError):
                validate_camera_probe({**probe, field: value}, dataset)


class ProcessTests(unittest.TestCase):
    def test_client_rejects_missing_tool_before_spawning(self):
        with tempfile.TemporaryDirectory(prefix="remi-surface-client-") as directory:
            root = Path(directory)
            source = root / "source.npz"
            np.savez(source, schema=np.array(1), **arrays())
            with mock.patch("integrations.splat_mesh.client.subprocess.Popen") as spawn, \
                    mock.patch("integrations.splat_mesh.client.platform_error", return_value=""):
                with self.assertRaisesRegex(ValueError, "Install Surface Tool"):
                    MeshJob.start(python=sys.executable, archive=source, output=root / "out",
                                  blender=sys.executable, tool_folder=root / "missing")
                spawn.assert_not_called()
            self.assertFalse((root / "out").exists())

    def test_client_requires_completion_not_just_an_output_file(self):
        with tempfile.TemporaryDirectory(prefix="remi-surface-result-") as directory:
            root = Path(directory)
            (root / "mesh.ply").write_text("partial output")
            log = (root / "worker.log").open("wb")
            process = mock.Mock()
            process.poll.return_value = 0
            job = MeshJob(process, root, log)
            with self.assertRaisesRegex(RuntimeError, "confirm completion"):
                job.result()
            job.cancel()
            self.assertFalse((root / "cancel.request").exists())

    def test_stage_cancellation_terminates_its_owned_child(self):
        with tempfile.TemporaryDirectory(prefix="remi-stage-cancel-") as directory:
            root = Path(directory)
            process = mock.Mock()
            process.poll.return_value = None
            calls = []

            def cancel(_output):
                calls.append(1)
                if len(calls) > 1:
                    raise InterruptedError("cancel")

            with mock.patch.object(pipeline, "check_cancel", side_effect=cancel), \
                    mock.patch.object(pipeline.subprocess, "Popen", return_value=process), \
                    mock.patch.object(pipeline, "terminate_tree") as terminate:
                with self.assertRaises(InterruptedError):
                    pipeline.run_stage(["owned-tool"], root / "stage.log", root, "Extracting…")
                terminate.assert_called_once_with(process)

    def test_completed_output_requires_a_matching_report_and_mesh_hash(self):
        with tempfile.TemporaryDirectory(prefix="remi-surface-report-") as directory:
            root = Path(directory)
            mesh = root / "mesh.ply"
            mesh.write_text("mesh fixture")
            (root / "complete.json").write_text(json.dumps({"schema": 1, "complete": True}))
            process = mock.Mock()
            process.poll.return_value = 0
            job = MeshJob(process, root, (root / "worker.log").open("wb"))
            with self.assertRaisesRegex(RuntimeError, "report"):
                job.result()
            report = {"schema": 2, "quality": "DENSE", "vertices": 3, "triangles": 1, "mesh_sha256": sha256(mesh)}
            (root / "report.json").write_text(json.dumps(report))
            self.assertEqual(job.result(), mesh)
            mesh.write_text("corrupted mesh")
            with self.assertRaisesRegex(RuntimeError, "report"):
                job.result()
            job.cancel()

    @unittest.skipUnless(os.name == "nt", "Windows process-tree cancellation")
    def test_cancel_terminates_worker_and_native_descendant(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        with tempfile.TemporaryDirectory(prefix="remi-owned-processes-") as directory:
            root = Path(directory)
            script = ("import subprocess,sys,time; from pathlib import Path; "
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
                      "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(120)")
            log = (root / "worker.log").open("wb")
            process = subprocess.Popen([sys.executable, "-c", script, str(root / "child.pid")],
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            handle = None
            try:
                deadline = time.monotonic() + 10
                while not (root / "child.pid").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                child_pid = int((root / "child.pid").read_text())
                handle = kernel.OpenProcess(0x00100000, False, child_pid)
                self.assertTrue(handle)
                MeshJob(process, root, log).cancel()
                self.assertIsNotNone(process.poll())
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
            finally:
                if process.poll() is None:
                    terminate_tree(process)
                if handle:
                    kernel.CloseHandle(handle)
                log.close()


if __name__ == "__main__":
    unittest.main()
