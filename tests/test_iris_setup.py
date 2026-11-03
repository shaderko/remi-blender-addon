"""Iris-3B integration metadata and config loading without Blender."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "integrations" / "iris3b" / "vendor"))

from integrations.iris3b import IrisJob, missing_components
from integrations.iris3b.constants import (
    MODEL_BYTES,
    MODEL_FILES,
    MODEL_REPO,
    MODEL_REVISION,
    SIZE_BUCKETS,
    TEXT_ENCODER_FILES,
    TEXT_ENCODER_REPO,
    TEXT_ENCODER_REVISION,
)


class IrisSetupTests(unittest.TestCase):
    def test_pinned_release_metadata(self):
        self.assertEqual(MODEL_FILES["base"], ("config.yaml", "model.safetensors"))
        self.assertIn("turbo/model.safetensors", MODEL_FILES["turbo"])
        self.assertEqual(MODEL_BYTES["base"], 11_950_112_680)
        self.assertEqual(MODEL_BYTES["turbo"], 5_975_089_200)
        self.assertTrue(MODEL_REPO.startswith("speridlabs/"))
        self.assertRegex(MODEL_REVISION, r"^[a-f0-9]{40}$")
        self.assertIn("model-00001-of-00002.safetensors", TEXT_ENCODER_FILES)
        self.assertEqual(TEXT_ENCODER_REPO, "Qwen/Qwen3-VL-4B-Instruct")
        self.assertRegex(TEXT_ENCODER_REVISION, r"^[a-f0-9]{40}$")
        self.assertEqual(SIZE_BUCKETS["SQUARE"], (1024, 1024))
        self.assertEqual(SIZE_BUCKETS["PORTRAIT"], (896, 1200))

    def test_missing_components_reports_model_and_encoder(self):
        with tempfile.TemporaryDirectory() as temporary:
            models = Path(temporary)
            missing = missing_components(models, "turbo")
            self.assertTrue(missing)
            self.assertTrue(any(str(Path("turbo/config.yaml")) in item for item in missing))
            self.assertTrue(any("Qwen3-VL" in item for item in missing))
            for relative in MODEL_FILES["turbo"]:
                path = models / "iris-3b" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            for relative in TEXT_ENCODER_FILES:
                path = models / "Qwen3-VL-4B-Instruct" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            self.assertEqual(missing_components(models, "turbo"), [])

    def test_job_validation_happens_before_spawn(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            with self.assertRaisesRegex(ValueError, "Missing model files"):
                IrisJob.start(
                    python=sys.executable,
                    models=temporary / "models",
                    output=temporary / "out",
                    variant="turbo",
                    prompt="a test",
                )

    def test_vendored_config_loader(self):
        from iris3b.config import load_config

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            path.write_text(
                "model:\n  hidden_size: 64\n  depth: 2\n  pixel:\n    depth: 1\n"
                "text_encoder:\n  dim: 64\n  hidden_layers: [2, 5]\n"
                "flow:\n  shift: 3.0\n",
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.model.hidden_size, 64)
            self.assertEqual(config.model.depth, 2)
            self.assertEqual(config.model.pixel.depth, 1)
            self.assertEqual(config.text_encoder.dim, 64)
            self.assertEqual(config.text_encoder.hidden_layers, [2, 5])
            self.assertEqual(config.flow.shift, 3.0)

    def test_worker_import_does_not_require_torch(self):
        spec = importlib.util.spec_from_file_location(
            "remi_iris_worker_test", ROOT / "integrations" / "iris3b" / "worker.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue((module.VENDOR / "iris3b" / "config.py").is_file())


if __name__ == "__main__":
    unittest.main()
