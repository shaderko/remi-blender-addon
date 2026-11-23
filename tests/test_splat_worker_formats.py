"""Cloud/local worker format compatibility; untrusted pickle stays disabled."""
import importlib.util
import json
from pathlib import Path
import pickle
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "integrations/splat_mesh"))
from integrations.triposplat.splat_data import load, save, save_native
from integrations.splat_project.worker import _load_splats as texture_load
from integrations.splat_mesh import splatmesh_worker as mesh_worker


def arrays():
    xyz = np.stack(np.meshgrid(*([[-.2, 0, .2]] * 3)), axis=-1).reshape(-1, 3)
    return {"positions": xyz.astype(np.float32),
            "scales": np.full((27, 3), .025, np.float32),
            "rotations": np.tile(np.array([1, 0, 0, 0], np.float32), (27, 1)),
            "opacities": np.ones(27, np.float32),
            "sh_dc": np.full((27, 3), -.7, np.float32)}


class WorkerFormatTests(unittest.TestCase):
    def test_texture_accepts_cloud_and_local_formats_by_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name, writer in (("local.npz", save), ("legacy.npz", lambda p, a: np.savez(p, **a)), ("cloud.splat", save_native),
                                 ("cloud-cache.npz", save_native)):
                with self.subTest(name=name):
                    path = Path(temporary) / name
                    writer(path, arrays())
                    for key, value in texture_load(path).items():
                        np.testing.assert_array_equal(value, arrays()[key])

    def test_invalid_and_pickled_files_never_invoke_pickle(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "untrusted.npz"
            with patch("pickle.load", side_effect=AssertionError("pickle executed")), \
                 patch("pickle.loads", side_effect=AssertionError("pickle executed")):
                for body in (pickle.dumps({"object": "data"}), b"glTF" + b"0" * 32,
                             b"<html>Download failed</html>", b""):
                    path.write_bytes(body)
                    with self.assertRaisesRegex(ValueError, "REMIGAUS"):
                        texture_load(path)
                np.savez(path, schema=1, **{**arrays(), "positions": np.array([object()])})
                with self.assertRaises(ValueError):
                    texture_load(path)
                save_native(path, arrays())
                path.write_bytes(path.read_bytes()[:-1])
                with self.assertRaisesRegex(ValueError, "byte length"):
                    texture_load(path)

    @unittest.skipUnless(importlib.util.find_spec("splatmesh"), "pinned remi-splat-mesh backend required")
    def test_mesh_accepts_both_formats_and_real_worker_extracts_cloud_splat(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, writer in (("local.npz", save), ("legacy.npz", lambda p, a: np.savez(p, **a)), ("cloud.splat", save_native)):
                path = root / name
                writer(path, arrays())
                splats = mesh_worker._load_splats(path)
                for key in ("positions", "scales", "rotations", "opacities"):
                    np.testing.assert_array_equal(getattr(splats, key), arrays()[key])
                np.testing.assert_array_equal(splats.colors, arrays()["sh_dc"])
            request = root / "request.json"
            request.write_text(json.dumps({"archive": str(root / "cloud.splat"),
                                           "quality": "FAST", "resolution": 64}))
            with patch.object(sys, "argv", ["worker", "--request", str(request)]):
                self.assertEqual(mesh_worker.main(), 0)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["gaussians"], 27)
            self.assertEqual(report["mesher"], "remi-adaptive-cpu-v1")
            self.assertGreater(report["adaptive"]["coarse_sample_count"], 0)
            self.assertGreater(report["adaptive"]["fine_surface_leaves"], 0)
            self.assertGreater(report["triangles"], 0)
            self.assertTrue((root / "complete.json").is_file())
            self.assertTrue((root / "mesh.ply").read_bytes().startswith(b"ply\n"))
            path = root / "bad.npz"
            np.savez(path, schema=1, **{**arrays(), "positions": np.array([object()])})
            with patch("pickle.load", side_effect=AssertionError("pickle executed")):
                with self.assertRaises(ValueError):
                    mesh_worker._load_splats(path)


if __name__ == "__main__":
    unittest.main()
