"""External worker test for Gaussian density meshing (no Blender)."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


splat_data = module("remi_splat_data", ROOT / "integrations" / "triposplat" / "splat_data.py")


def available():
    return all(importlib.util.find_spec(name) for name in ("torch", "scipy", "trimesh"))


def synthetic_sphere(path, count=3000, radius=0.4):
    indices = np.arange(count, dtype=np.float64) + 0.5
    phi = np.arccos(1 - 2 * indices / count)
    theta = np.pi * (1 + np.sqrt(5.0)) * indices
    positions = np.stack((
        radius * np.sin(phi) * np.cos(theta),
        radius * np.sin(phi) * np.sin(theta),
        radius * np.cos(phi),
    ), axis=1).astype(np.float32)
    arrays = {
        "positions": positions,
        "scales": np.full((count, 3), 0.025, dtype=np.float32),
        "rotations": np.tile(np.array([1, 0, 0, 0], dtype=np.float32), (count, 1)),
        "opacities": np.ones(count, dtype=np.float32),
        "sh_dc": np.full((count, 3), 0.0, dtype=np.float32),
    }
    splat_data.save(path, arrays)
    return path


@unittest.skipUnless(available(), "torch/scipy/trimesh are required for the external mesher")
class MeshWorkerTests(unittest.TestCase):
    def test_synthetic_sphere_becomes_a_colored_mesh(self):
        with tempfile.TemporaryDirectory(prefix="remi-splat-mesh-") as directory:
            root = Path(directory)
            archive = synthetic_sphere(root / "splats.npz")
            request = root / "request.json"
            request.write_text(json.dumps({
                "archive": str(archive), "resolution": 128, "taubin": 6,
                "min_component": 100,
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-u",
                  str(ROOT / "integrations" / "splat_mesh" / "density_worker.py"),
                 "--request", str(request)],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "report.json").read_text())
            self.assertGreater(report["triangles"], 1000)
            self.assertTrue((root / "complete.json").is_file())
            self.assertTrue((root / "mesh.ply").is_file())
            import trimesh
            mesh = trimesh.load(root / "mesh.ply", process=False)
            self.assertGreater(len(mesh.vertices), 100)
            self.assertIsNotNone(mesh.visual.vertex_colors)

    def test_missing_archive_fails_before_torch(self):
        with tempfile.TemporaryDirectory(prefix="remi-splat-mesh-") as directory:
            root = Path(directory)
            request = root / "request.json"
            request.write_text(json.dumps({"archive": str(root / "missing.npz")}))
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-u",
                  str(ROOT / "integrations" / "splat_mesh" / "density_worker.py"),
                 "--request", str(request)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing.npz", json.loads((root / "status.json").read_text())["error"])


if __name__ == "__main__":
    unittest.main()
