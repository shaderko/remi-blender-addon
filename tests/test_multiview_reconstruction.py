"""CPU checks for posed-depth reconstruction; no downloads or network inference."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_stereo import sha256
from splat_reconstruction.geometry import make_rig
from splat_reconstruction.multiview import (
    consistent_depth, nearest_context, resize_input, training_cameras, valid_prediction,
)
from fuse_multiview_depth import fuse, load_frames
from prepare_multiview import verify_bundle, SOURCE_COMMIT, MODEL_COMMIT
from splat_reconstruction.fusion import TSDF


BOUNDS = {"low": [-0.6] * 3, "high": [0.6] * 3, "center": [0] * 3, "radius": 1.04}


def plane_frame(translation=0, depth=2.0):
    camera = np.eye(4)
    camera[0, 3] = translation
    return {"depth": np.full((32, 32), depth, np.float32),
            "confidence": np.ones((32, 32), np.float32), "camera": camera,
            "intrinsics": np.array([[20, 0, 15.5], [0, 20, 15.5], [0, 0, 1]])}


class InputTests(unittest.TestCase):
    def test_resize_preserves_integer_center_camera(self):
        rgba = np.full((112, 112, 4), 255, np.uint8)
        k = np.array([[100, 0, 55.5], [0, 100, 55.5], [0, 0, 1]])
        rgb, alpha, result = resize_input(rgba, k, 56)
        self.assertEqual(rgb.shape, (56, 56, 3))
        np.testing.assert_allclose(result, [[50, 0, 27.5], [0, 50, 27.5], [0, 0, 1]])
        np.testing.assert_array_equal(alpha, 1)
        np.testing.assert_allclose(k[0], [100, 0, 55.5])

    def test_alpha_compositing_does_not_expose_hidden_rgb(self):
        rgba = np.full((56, 56, 4), 255, np.uint8)
        rgba[..., 3] = 0
        rgb, alpha, _ = resize_input(rgba, np.eye(3), 56)
        np.testing.assert_array_equal(rgb, 127)
        np.testing.assert_array_equal(alpha, 0)

    def test_invalid_resolution_and_nonsquare_are_rejected(self):
        for shape, resolution in (((56, 56, 4), 55), ((56, 112, 4), 56)):
            with self.assertRaises(ValueError):
                resize_input(np.zeros(shape, np.uint8), np.eye(3), resolution)

    def test_nonfinite_depth_confidence_and_background_are_invalid(self):
        depth = np.array([[2, np.nan, 0, 10, 2, 2]], dtype=float)
        score = np.array([[4, 4, 4, 4, np.inf, 4]], dtype=float)
        alpha = np.array([[1, 1, 1, 1, 1, 0]], dtype=float)
        d, w = valid_prediction(depth, score, alpha, 0.1, 5)
        np.testing.assert_array_equal(d, [[2, 0, 0, 0, 0, 0]])
        np.testing.assert_allclose(w, [[0.8, 0, 0, 0, 0, 0]])
        self.assertTrue(np.isfinite(w).all())

    def test_semtransparent_thin_pixels_are_not_percentile_discarded(self):
        depth, weight = valid_prediction(np.ones((2, 2)), np.ones((2, 2)),
                                         np.full((2, 2), 0.25), 0.1, 5)
        self.assertTrue((depth > 0).all())
        self.assertTrue((weight > 0).all())

    def test_context_contains_only_training_cameras(self):
        rig = make_rig(BOUNDS, views=8, evaluation_views=2, resolution=112)
        cameras = training_cameras(rig)
        selected = nearest_context(cameras, 3, 5)
        self.assertEqual(selected[0], 3)
        self.assertEqual(len(set(selected)), 5)
        self.assertTrue(all(c["rgb"].startswith("pairs/") for c in cameras))
        self.assertEqual(selected, nearest_context(cameras, 3, 5))
        rig["pairs"][0]["left"] = rig["evaluation_views"][0]["rgb"]
        with self.assertRaisesRegex(ValueError, "overlap"):
            training_cameras(rig)


class ConsistencyTests(unittest.TestCase):
    def test_surface_band_ablation_does_not_observe_distant_free_space(self):
        bounds = {"low": [-0.1, -0.1, 1.0], "high": [0.1, 0.1, 2.5]}
        frame = plane_frame()
        normal, band = TSDF(bounds, 32), TSDF(bounds, 32)
        normal.integrate(frame["depth"], frame["confidence"], frame["camera"], frame["intrinsics"])
        band.integrate(frame["depth"], frame["confidence"], frame["camera"], frame["intrinsics"], surface_band_only=True)
        self.assertLess((band.weights > 0).sum(), (normal.weights > 0).sum())
        z = band.origin[2] + np.arange(band.shape[2]) * band.voxel
        self.assertFalse(band.weights[:, :, z < 2 - band.truncation - 1e-6].any())

    def test_calibrated_parallel_plane_is_supported_without_changing_depth(self):
        target, other = plane_frame(), plane_frame(translation=0.2)
        depth, weight, support = consistent_depth(target, [other])
        # 20 * 0.2 / 2 = two pixels of horizontal displacement.
        np.testing.assert_array_equal(depth[:, :2], 0)
        np.testing.assert_array_equal(depth[:, 2:], 2)
        np.testing.assert_array_equal(weight[:, 2:], 1)
        np.testing.assert_array_equal(support[:, 2:], 1)
        np.testing.assert_array_equal(target["depth"], 2)

    def test_occluder_and_invalid_neighbor_do_not_support_a_surface(self):
        target, occluder, invalid = plane_frame(), plane_frame(depth=1), plane_frame(depth=0)
        depth, weight, support = consistent_depth(target, [occluder, invalid])
        self.assertFalse(depth.any())
        self.assertFalse(weight.any())
        self.assertFalse(support.any())

    def test_other_views_count_excludes_the_target(self):
        target = plane_frame()
        depth, _, support = consistent_depth(target, [plane_frame(), plane_frame(depth=1)], min_support=2)
        self.assertFalse(depth.any())
        np.testing.assert_array_equal(support, 1)

    def test_backprojection_rejects_depth_that_moves_to_another_pixel(self):
        target, other = plane_frame(), plane_frame(translation=0.5, depth=3)
        depth, _, _ = consistent_depth(target, [other], relative_tolerance=1, pixel_tolerance=0.1)
        self.assertFalse(depth.any())

    def test_empty_target_and_bad_thresholds(self):
        target = plane_frame(depth=0)
        depth, _, _ = consistent_depth(target, [plane_frame()])
        self.assertFalse(depth.any())
        for kwargs in ({"min_support": 0}, {"min_support": 2}, {"relative_tolerance": -1},
                       {"pixel_tolerance": 0}, {"relative_tolerance": np.nan}):
            with self.assertRaises(ValueError):
                consistent_depth(target, [plane_frame()], **kwargs)


class DatasetTests(unittest.TestCase):
    def test_end_to_end_analytic_depth_fusion_keeps_source_and_reports_no_postprocessing(self):
        with tempfile.TemporaryDirectory(prefix="remi-da3-fusion-") as directory:
            root = Path(directory)
            views, depths = root / "views", root / "depths"
            views.mkdir()
            depths.mkdir()
            archive = root / "archive.npz"
            np.savez(archive, positions=np.zeros((1, 3)))
            original_hash = sha256(archive)
            dataset = make_rig(BOUNDS, views=12, evaluation_views=1, resolution=112)
            dataset.update(source_archive=str(archive), source_sha256=original_hash)
            dataset_path = views / "dataset.json"
            dataset_path.write_text(json.dumps(dataset))
            _, _, k = resize_input(np.zeros((112, 112, 4), np.uint8), dataset["intrinsics"], 56)
            yy, xx = np.mgrid[:56, :56]
            rays = np.stack(((xx - k[0, 2]) / k[0, 0], (yy - k[1, 2]) / k[1, 1], np.ones_like(xx)), axis=-1)
            records = []
            for pair in dataset["pairs"]:
                camera = np.asarray(pair["left_camera_to_world_opencv"])
                direction, origin = rays @ camera[:3, :3].T, camera[:3, 3]
                a, b, c = (direction * direction).sum(-1), 2 * (direction * origin).sum(-1), origin @ origin - 0.5 ** 2
                discriminant = b * b - 4 * a * c
                depth = np.where(discriminant >= 0, (-b - np.sqrt(np.maximum(discriminant, 0))) / (2 * a), 0)
                name = f"view_{pair['index']:04d}.npz"
                np.savez(depths / name, depth=depth, confidence=(depth > 0).astype(float),
                         rgb=np.full((56, 56, 3), 127, np.uint8), intrinsics=k, camera=camera)
                records.append({"index": pair["index"], "file": name, "rgb": pair["left"]})
            (depths / "depths.json").write_text(json.dumps({
                "dataset": str(views), "dataset_sha256": sha256(dataset_path),
                "source_sha256": original_hash, "resolution": 56, "frames": records}))
            with mock.patch("builtins.print"):
                report = fuse(views, depths, root / "mesh", resolution=32, raw=True)
            self.assertGreater(report["topology"]["triangles"], 500)
            self.assertTrue(report["postprocessing"].startswith("none"))
            self.assertTrue((root / "mesh/mesh.ply").is_file())
            self.assertEqual(original_hash, sha256(archive))
            with self.assertRaisesRegex(ValueError, "not empty"):
                fuse(views, depths, root / "mesh", resolution=32)

    def test_prepared_source_modification_and_untracked_code_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="remi-da3-verification-") as directory:
            root = Path(directory)
            files = {}
            for name in ("source/src/depth_anything_3/api.py", "source/LICENSE",
                         "model/config.json", "model/model.safetensors"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test fixture, not executable model code")
                files[name] = {"sha256": sha256(path)}
            (root / "provenance.json").write_text(json.dumps({
                "source_commit": SOURCE_COMMIT, "model_commit": MODEL_COMMIT, "files": files}))
            with mock.patch("prepare_multiview.WEIGHTS_SHA256", files["model/model.safetensors"]["sha256"]):
                verify_bundle(root)
                extra = root / "source/src/depth_anything_3/injected.py"
                extra.write_text("# unexpected source")
                with self.assertRaisesRegex(ValueError, "Untracked"):
                    verify_bundle(root)
            (root / "source/src/depth_anything_3/api.py").write_text("# modified source")
            with self.assertRaisesRegex(ValueError, "modified"):
                verify_bundle(root)

    def test_fusion_rejects_mixed_calibration_and_modified_dataset(self):
        with tempfile.TemporaryDirectory(prefix="remi-multiview-") as directory:
            root = Path(directory)
            views, depths = root / "views", root / "depths"
            views.mkdir()
            depths.mkdir()
            archive = root / "archive.npz"
            np.savez(archive, positions=np.zeros((1, 3)))
            dataset = make_rig(BOUNDS, views=3, evaluation_views=1, resolution=112)
            dataset.update(source_archive=str(archive), source_sha256=sha256(archive))
            dataset_path = views / "dataset.json"
            dataset_path.write_text(json.dumps(dataset))
            records = []
            # Get the correct resize from the actual stored render intrinsics.
            _, _, k = resize_input(np.zeros((112, 112, 4), np.uint8), dataset["intrinsics"], 56)
            for pair in dataset["pairs"]:
                name = f"view_{pair['index']:04d}.npz"
                np.savez(depths / name, depth=np.ones((56, 56)), confidence=np.ones((56, 56)),
                         rgb=np.ones((56, 56, 3), np.uint8), intrinsics=k,
                         camera=pair["left_camera_to_world_opencv"])
                records.append({"index": pair["index"], "file": name, "rgb": pair["left"]})
            report = {"dataset": str(views), "dataset_sha256": sha256(dataset_path),
                      "source_sha256": dataset["source_sha256"], "resolution": 56, "frames": records}
            (depths / "depths.json").write_text(json.dumps(report))
            self.assertEqual(len(load_frames(views, depths)[3]), 3)
            with np.load(depths / records[0]["file"]) as data:
                arrays = dict(data)
            arrays["intrinsics"] = np.eye(3)
            np.savez(depths / records[0]["file"], **arrays)
            with self.assertRaisesRegex(ValueError, "calibration"):
                load_frames(views, depths)
            dataset_path.write_text(json.dumps({**dataset, "samples": 999}))
            with self.assertRaisesRegex(ValueError, "frozen"):
                load_frames(views, depths)


if __name__ == "__main__":
    unittest.main()
