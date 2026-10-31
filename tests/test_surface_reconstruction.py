"""Safety/config tests for the external surface experiment, never executing it."""
from pathlib import Path
import hashlib
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_surface_optimizer as prepare_tool
from run_surface_reconstruction import optimizer_environment, parse_camera_probe, train_arguments, validate_camera_probe
from constrain_mesh_silhouettes import constrain, silhouette_support
from prepare_stereo import sha256


class SurfaceExperimentTests(unittest.TestCase):
    def test_gate_checks_face_interiors_preserves_inputs_and_never_reads_holdouts(self):
        import trimesh
        with tempfile.TemporaryDirectory(prefix="remi-silhouette-gate-") as directory:
            root = Path(directory)
            views = root / "views"
            views.mkdir()
            archive = root / "archive.npz"
            np.savez(archive, positions=np.zeros((1, 3)))
            rgba = np.zeros((32, 32, 4), np.uint8)
            rgba[14:18, 14:18] = 255
            for y, x in ((16, 6), (16, 26), (26, 16)):
                rgba[y - 1:y + 2, x - 1:x + 2] = 255
            pairs = []
            for i in range(3):
                name = f"training_{i}.png"
                Image.fromarray(rgba).save(views / name)
                pairs.append({"index": i, "left": name, "left_camera_to_world_opencv": np.eye(4).tolist()})
            dataset = {"source_archive": str(archive), "source_sha256": sha256(archive),
                       "resolution": [32, 32], "intrinsics": [[10, 0, 15.5], [0, 10, 15.5], [0, 0, 1]],
                       "near": 0.1, "pairs": pairs, "evaluation_views": [{"rgb": "missing_holdout.png"}]}
            (views / "dataset.json").write_text(json.dumps(dataset))
            (views / "render_complete.json").write_text('{"complete":true}')
            mesh = trimesh.Trimesh(vertices=[[-0.2, -0.2, 2], [0.2, -0.2, 2], [0, 0.2, 2],
                                            [-2, 0, 2], [2, 0, 2], [0, 2, 2]],
                                   faces=[[0, 1, 2], [3, 4, 5]], process=False)
            source = root / "source.ply"
            mesh.export(source)
            original_hash = sha256(source)
            with mock.patch("builtins.print"):
                report = constrain(views, source, root / "out", padding=0, max_background=0, device="cpu")
            self.assertEqual(report["rejected_faces"], 1)
            self.assertEqual(report["after"]["triangles"], 1)
            self.assertEqual(report["after"]["boundary_edges"], 3)
            self.assertEqual(sha256(source), original_hash)
            self.assertFalse((views / "missing_holdout.png").exists())

    def test_silhouettes_reject_background_and_unknown_points_without_inventing_geometry(self):
        camera = np.eye(4)
        k = np.array([[10, 0, 15.5], [0, 10, 15.5], [0, 0, 1]])
        mask = np.zeros((32, 32), bool)
        mask[12:20, 12:20] = True
        points = np.array([[0, 0, 2], [2, 0, 2], [100, 0, 2], [0, 0, -2]])
        accepted = silhouette_support(points, [camera] * 3, [mask] * 3, k, 0.1,
                                      max_background=0, min_foreground=3, device="cpu")
        np.testing.assert_array_equal(accepted, [True, False, False, False])
        with self.assertRaises(ValueError):
            silhouette_support(points, [camera], [mask], k, 0.1, min_foreground=2, device="cpu")

    def test_external_camera_changes_are_a_hard_blocker(self):
        dataset = {"pairs": [{"left_camera_to_world_opencv": np.eye(4).tolist()}],
                   "intrinsics": [[100, 0, 9.5], [0, 100, 9.5], [0, 0, 1]], "resolution": [20, 20]}
        probe = {"viewmats": np.eye(4).ravel().tolist(), "train_frame_scale": 1,
                 "intrins": [100, 100, 9.5, 9.5], "widths": [20], "heights": [20]}
        validate_camera_probe(probe, dataset)
        with self.assertRaisesRegex(RuntimeError, "rescaled"):
            validate_camera_probe({**probe, "train_frame_scale": 2}, dataset)
        with self.assertRaisesRegex(RuntimeError, "intrinsics"):
            validate_camera_probe({**probe, "intrins": [100, 100, 10, 10]}, dataset)
        moved = np.eye(4)
        moved[0, 3] = 0.1
        with self.assertRaisesRegex(RuntimeError, "camera frame"):
            validate_camera_probe({**probe, "viewmats": moved.ravel().tolist()}, dataset)

    def test_overlay_workaround_changes_only_child_environment(self):
        parent = {"PATH": "preserve", "DISABLE_VULKAN_OW_OVERLAY_LAYER": "0"}
        child = optimizer_environment(parent)
        self.assertEqual(parent, {"PATH": "preserve", "DISABLE_VULKAN_OW_OVERLAY_LAYER": "0"})
        self.assertEqual(child["PATH"], "preserve")
        self.assertEqual(child["DISABLE_VULKAN_OW_OVERLAY_LAYER"], "1")
        self.assertEqual(child["DISABLE_VULKAN_OW_OBS_CAPTURE"], "1")

    def test_numeric_probe_parsing_does_not_depend_on_broken_windows_filename_json(self):
        probe = {'viewmats': list(range(16)), 'intrins': [100, 100, 10, 10],
                 'widths': [20], 'heights': [20], 'train_frame_scale': 1}
        text = '{"image_filenames": ["C:\\unescaped\\path.png"], ' + json.dumps(probe)[1:]
        self.assertEqual(parse_camera_probe(text), probe)
        with self.assertRaises(ValueError):
            parse_camera_probe(text.replace('"train_frame_scale": 1', '"train_frame_scale": NaN'))

    def test_explicit_training_settings_disable_network_and_coordinate_changes(self):
        args = train_arguments("spirula.exe", Path("data"), Path("source.ply"), Path("out"), "fit", 8000, 1000000)
        flags = dict(zip(args[3::2], args[4::2]))
        self.assertEqual(flags["--disable-viewer"], "1")
        self.assertEqual(flags["--keep-viewer-alive"], "0")
        self.assertEqual(flags["--auto-scale-poses"], "0")
        self.assertEqual(flags["--scene-center"], "none")
        self.assertEqual(flags["--relative-scale"], "1")
        self.assertEqual(flags["--load-depths"], "0")
        self.assertEqual(flags["--load-normals"], "0")
        self.assertEqual(flags["--cap-max"], "1000000")
        self.assertNotIn("--quality", flags)

    def test_existing_tool_folder_is_not_overwritten_or_downloaded_into(self):
        with tempfile.TemporaryDirectory(prefix="remi-surface-tool-") as directory:
            path = Path(directory)
            sentinel = path / "user.txt"
            sentinel.write_text("preserve")
            with mock.patch.object(prepare_tool.urllib.request, "urlopen") as request:
                with self.assertRaisesRegex(ValueError, "fresh"):
                    prepare_tool.prepare(path)
                request.assert_not_called()
            self.assertEqual(sentinel.read_text(), "preserve")

    def test_archive_traversal_is_rejected_even_with_matching_digest(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../outside.exe", b"not an executable")
        data = payload.getvalue()
        with tempfile.TemporaryDirectory(prefix="remi-surface-archive-") as directory:
            with mock.patch.object(prepare_tool, "DIGEST", hashlib.sha256(data).hexdigest()), \
                    mock.patch.object(prepare_tool, "SIZE", len(data)), \
                    mock.patch.object(prepare_tool.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    prepare_tool.prepare(Path(directory) / "tool")
            self.assertFalse((Path(directory) / "outside.exe").exists())


if __name__ == "__main__":
    unittest.main()
