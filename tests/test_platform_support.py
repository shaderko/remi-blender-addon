"""Packaging and process-liveness checks, runnable without Blender."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load_module("remi_release", ROOT / "scripts" / "release.py")
with mock.patch.dict(sys.modules, {"bpy": types.ModuleType("bpy")}):
    disk = load_module("remi_disk", ROOT / "workflow" / "disk.py")


class ProcessTests(unittest.TestCase):
    def test_child_survives_liveness_probe(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            self.assertTrue(disk.SessionDiskService._pid_is_alive(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=10)
        self.assertFalse(disk.SessionDiskService._pid_is_alive(child.pid))

    def test_windows_query_closes_handle_and_preserves_uncertain_owners(self):
        import ctypes
        kernel = mock.Mock()
        kernel.OpenProcess.return_value = 123
        with mock.patch.object(disk.os, "name", "nt"), \
             mock.patch.object(ctypes, "WinDLL", return_value=kernel, create=True), \
             mock.patch.object(ctypes, "get_last_error", create=True) as error, \
             mock.patch.object(disk.os, "kill") as kill:
            for status, alive in ((258, True), (0, False), (0xFFFFFFFF, True)):
                kernel.WaitForSingleObject.return_value = status
                self.assertEqual(disk.SessionDiskService._pid_is_alive(456), alive)
            self.assertEqual(kernel.CloseHandle.call_count, 3)
            kernel.OpenProcess.assert_called_with(0x00100000, False, 456)
            kernel.OpenProcess.return_value = None
            for code, alive in ((87, False), (5, True), (8, True)):
                error.return_value = code
                self.assertEqual(disk.SessionDiskService._pid_is_alive(456), alive)
            self.assertEqual(kernel.CloseHandle.call_count, 3)
            kill.assert_not_called()


class PackagingTests(unittest.TestCase):
    def test_each_archive_stages_only_its_platform(self):
        for target, suffix in (("windows-x64", ".cp313-win_amd64.pyd"),
                               ("macos-arm64", ".cpython-313-darwin.so")):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "source"
                root.mkdir()
                (root / "blender_manifest.toml").write_bytes((ROOT / "blender_manifest.toml").read_bytes())
                (root / "wheels").mkdir()
                (root / "wheels" / "lock.json").write_bytes((ROOT / "wheels" / "lock.json").read_bytes())
                for directory, module in release.NATIVE_MODULES.items():
                    native = root / directory / "_native"
                    native.mkdir(parents=True)
                    (native / (module + suffix)).write_bytes(b"test target binary")
                    (native / "foreign.so").write_bytes(b"foreign")
                    (native / "foreign.pyd").write_bytes(b"foreign")
                    (root / directory / "native").mkdir()
                    (root / directory / "native" / "build-cache").write_text("excluded")
                wheel = root / "wheels" / release.wheel_spec(target)["filename"]
                wheel.write_bytes(b"test target wheel")
                (root / "wheels" / "foreign.whl").write_bytes(b"foreign")
                stage = Path(temporary) / "stage"
                with mock.patch.object(release, "ROOT", root):
                    release.stage_extension(stage, target, wheel)
                data = tomllib.loads((stage / "blender_manifest.toml").read_text())
                self.assertEqual(data["platforms"], [target])
                self.assertEqual(data["wheels"], ["./wheels/" + wheel.name])
                self.assertEqual(list((stage / "wheels").glob("*.whl")), [stage / "wheels" / wheel.name])
                self.assertEqual(len(list(stage.rglob("*" + suffix))), 2)
                self.assertFalse(list(stage.rglob("foreign.*")))
                self.assertFalse(list(stage.rglob("build-cache")))
                missing = root / next(iter(release.NATIVE_MODULES)) / "_native"
                next(missing.glob("*" + suffix)).unlink()
                with mock.patch.object(release, "ROOT", root):
                    with self.assertRaisesRegex(RuntimeError, "Missing"):
                        release.stage_extension(Path(temporary) / "missing", target, wheel)

    def test_unsupported_hosts_fail(self):
        with mock.patch.object(release.platform, "system", return_value="Windows"), \
             mock.patch.object(release.platform, "machine", return_value="ARM64"):
            with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                release.host_platform()


if __name__ == "__main__":
    unittest.main()
