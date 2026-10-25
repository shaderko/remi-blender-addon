"""Storage installation tests; no network, Blender, torch, or multi-GB weights."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "remi_download_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "integrations" / "triposplat")]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(f"{PACKAGE}.download", Path(package.__path__[0]) / "download.py")
download = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = download
spec.loader.exec_module(download)
client = __import__(f"{PACKAGE}.client", fromlist=["client"])
BASE = "https://models.example/models/release/blender/"


def fixture():
    files = {name: f"weight-{index}".encode() for index, name in enumerate(download.COMPONENTS)}
    manifest = {"schema": 1, "model": "rand6_100_ncm", "files": {
        name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files.items()
    }}
    return {"bundle.json": json.dumps(manifest).encode(), **files}


class ModelDownloadTests(unittest.TestCase):
    def test_default_release_is_public_step_1500(self):
        self.assertIn("remipublic.hel1.your-objectstorage.com", download.DEFAULT_MODEL_BASE)
        self.assertIn("step1500-af36bcb6-v1/blender/", download.DEFAULT_MODEL_BASE)

    def setUp(self):
        self.files = fixture()
        self.calls = []

    def opener(self, url):
        self.assertTrue(url.startswith(BASE))
        name = url.removeprefix(BASE)
        self.calls.append(name)
        return io.BytesIO(self.files[name])

    def test_stream_verify_install_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phases = []
            # macOS /var aliases and Windows short paths resolve to different spellings.
            self.assertEqual(download.download_models(BASE, root, phases.append, opener=self.opener), root.resolve())
            for name in download.COMPONENTS:
                self.assertEqual((root / name).read_bytes(), self.files[name])
            self.assertEqual(list(root.rglob("*.partial")), [])
            self.calls.clear()
            download.download_models(BASE, root, opener=self.opener)
            self.assertEqual(self.calls, ["bundle.json"])
            self.assertEqual(phases[-1], "Models downloaded and verified")

    def test_truncated_http_response_resumes_at_the_exact_offset(self):
        data = b"complete weights"
        first, tail = io.BytesIO(data[:5]), io.BytesIO(data[5:])
        first.headers = {"Content-Length": str(len(data))}
        tail.status = 206
        tail.headers = {"Content-Range": f"bytes 5-{len(data) - 1}/{len(data)}"}
        opener = mock.Mock(side_effect=[first, tail])
        self.assertEqual(b"".join(download.download_chunks(BASE + "weights", len(data), opener)), data)
        self.assertEqual(opener.call_args.args[0].get_header("Range"), "bytes=5-")

    def test_unhonored_resume_range_is_rejected(self):
        first, tail = io.BytesIO(b"part"), io.BytesIO(b"whole-file-again")
        first.headers = {"Content-Length": "10"}
        tail.status = 200
        tail.headers = {}
        with self.assertRaisesRegex(ValueError, "invalid resumed"):
            list(download.download_chunks(BASE + "weights", 10, mock.Mock(side_effect=[first, tail])))

    def test_invalid_downloads_never_become_model_files(self):
        for replacement in (b"short", b"x" * 100, b"invalid!"):
            with self.subTest(data=replacement), tempfile.TemporaryDirectory() as directory:
                self.files = fixture()
                self.files[download.COMPONENTS[0]] = replacement
                root = Path(directory)
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    download.download_models(BASE, root, opener=self.opener)
                self.assertFalse((root / download.COMPONENTS[0]).exists())
                self.assertEqual(list(root.rglob("*.partial")), [])

    def test_conflicting_local_files_are_preserved_before_any_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / download.COMPONENTS[-1]
            path.parent.mkdir(parents=True)
            path.write_bytes(b"user supplied weights")
            with self.assertRaisesRegex(ValueError, "Existing model differs"):
                download.download_models(BASE, root, opener=self.opener)
            self.assertEqual(path.read_bytes(), b"user supplied weights")
            self.assertEqual(self.calls, ["bundle.json"])

    def test_retry_reuses_completed_files_after_network_failure(self):
        def interrupted(url):
            if url.endswith(download.COMPONENTS[1]):
                raise OSError("network lost")
            return self.opener(url)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(OSError, "network lost"):
                download.download_models(BASE, root, opener=interrupted)
            self.assertTrue((root / download.COMPONENTS[0]).is_file())
            self.calls.clear()
            download.download_models(BASE, root, opener=self.opener)
            self.assertNotIn(download.COMPONENTS[0], self.calls)

    def test_untrusted_manifest_paths_and_bad_metadata_rejected(self):
        for name, metadata in (("../outside.pt", {"bytes": 1, "sha256": "a" * 64}),
                               (download.COMPONENTS[0], {"bytes": True, "sha256": "a" * 64}),
                               (download.COMPONENTS[0], {"bytes": 1, "sha256": "bad"})):
            manifest = json.loads(fixture()["bundle.json"])
            manifest["files"][name] = metadata
            with self.subTest(name=name), self.assertRaises(ValueError):
                download.validate_manifest(manifest)

    def test_https_only_no_credentials_query_or_fragment(self):
        self.assertEqual(download.model_base(BASE.rstrip("/")), BASE)
        for url in ("", "http://models.example/", "file:///models", "https://key:secret@models.example/",
                    BASE + "?signed=secret", BASE + "#fragment"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                download.model_base(url)
        with self.assertRaises(ValueError):
            download.HTTPSRedirectHandler().redirect_request(
                urllib.request.Request(BASE), None, 302, "Found", {}, "http://models.example/weights")

    def test_path_resolution_cannot_escape_model_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                download.destination(Path(directory), "../outside")

    def test_concurrently_created_conflicting_target_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporary, target = root / "partial", root / "best_weights.pt"
            temporary.write_bytes(b"downloaded")
            target.write_bytes(b"user weights")
            with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                download.install_verified(temporary, target, hashlib.sha256(b"downloaded").hexdigest())
            self.assertEqual(target.read_bytes(), b"user weights")

    def test_cancel_reaps_process_and_removes_only_owned_partials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = mock.Mock()
            process.poll.return_value = None
            with mock.patch.object(client.subprocess, "Popen", return_value=process) as spawn:
                job = client.ModelDownloadJob.start(python=sys.executable, models=root, base_url=BASE)
            command = spawn.call_args.args[0]
            self.assertEqual(command[1:4], ["-I", "-B", "-u"])
            self.assertNotIn("shell", spawn.call_args.kwargs)
            owned = download.partial_path(root, download.COMPONENTS[0], job.token)
            owned.write_bytes(b"incomplete")
            other = root / ".unrelated.partial"
            other.write_bytes(b"user")
            job.cancel()
            process.terminate.assert_called_once()
            self.assertFalse(owned.exists())
            self.assertTrue(other.exists())

    def test_isolated_script_starts_without_torch_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([
                sys.executable, "-I", "-B", str(Path(download.__file__)),
                "--base-url", "http://not-https.example/", "--models", directory,
                "--status-dir", directory, "--token", "a" * 32,
            ], capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            state = json.loads((Path(directory) / "status.json").read_text())
            self.assertIn("HTTPS", state["error"])
            self.assertFalse((Path(directory) / "complete.json").exists())


if __name__ == "__main__":
    unittest.main()
