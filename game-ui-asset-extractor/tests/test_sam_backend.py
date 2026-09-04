"""Frozen v0.1 contract tests for the SAM1 ViT-B box-only backend.

These tests never load a real checkpoint: the SamPredictor and SAM model
registry are faked, so the default suite verifies orchestration contracts
only. A real-checkpoint integration test exists but is skipped unless the
``SAM_INTEGRATION_CHECKPOINT`` environment variable points at a local
checkpoint file.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SCRIPTS = ROOT / "scripts"
for path in (str(SCRIPTS),):
    if path not in sys.path:
        sys.path.insert(0, path)

import extract_assets as extractor  # noqa: E402
import sam_backend  # noqa: E402


BACKGROUND = (14, 22, 38, 255)
FOREGROUND = (235, 205, 70, 255)


def sam_request(source: Path, assets: list[dict], config: dict | None = None) -> dict:
    document = {
        "schema_version": "0.1",
        "source_image": str(source),
        "config": {"backend": "sam1_vit_b", "sam_checkpoint": "fake.pth"},
        "assets": assets,
    }
    if config is not None:
        document["config"].update(config)
    return document


def asset_entry(asset_id: str, bbox: dict[str, int]) -> dict:
    return {
        "asset_id": asset_id,
        "asset_type": "icon",
        "final_bbox": bbox,
        "extraction_mode": "foreground_extract",
    }


class FakeSamPredictor:
    """Records orchestration calls; returns scripted masks and scores.

    ``set_image_calls`` counts encodes so tests can assert the encode-once
    performance contract. ``predict_calls`` captures the exact kwargs so
    tests can assert the box-only prompt contract.
    """

    def __init__(self, masks: np.ndarray, scores: np.ndarray, handler=None):
        self.masks = masks
        self.scores = scores
        self.handler = handler  # optional (box) -> (masks, scores)
        self.set_image_calls = 0
        self.predict_calls: list[dict] = []
        self.encoded_shape: tuple | None = None

    def set_image(self, image: np.ndarray) -> None:
        self.set_image_calls += 1
        self.encoded_shape = image.shape

    def predict(self, **kwargs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        self.predict_calls.append(kwargs)
        if self.handler is not None:
            masks, scores = self.handler(kwargs["box"])
        else:
            masks, scores = self.masks, self.scores
        count = masks.shape[0]
        return (
            masks,
            scores,
            np.zeros((count, 256, 256), dtype=np.float32),
        )


def patch_sam_loader(testcase, predictor: FakeSamPredictor, info: dict | None = None):
    load_info = info or {
        "model": "sam1_vit_b",
        "model_type": "vit_b",
        "checkpoint": "fake.pth",
        "requested_device": "auto",
        "device": "cpu",
        "device_fallback": False,
    }

    def fake_load(model_type, checkpoint, device):
        return predictor, load_info

    original = sam_backend.load_sam_predictor
    sam_backend.load_sam_predictor = fake_load
    testcase.addCleanup(setattr, sam_backend, "load_sam_predictor", original)


def solid_source(path: Path) -> dict[str, int]:
    pixels = np.full((80, 100, 4), BACKGROUND, dtype=np.uint8)
    pixels[30:50, 40:60] = FOREGROUND
    Image.fromarray(pixels, "RGBA").save(path)
    return {"x": 36, "y": 26, "width": 28, "height": 28}


def full_size_masks(shape: tuple[int, int], pixel_boxes: list[tuple[int, int, int, int]]) -> np.ndarray:
    masks = np.zeros((len(pixel_boxes), shape[0], shape[1]), dtype=bool)
    for index, (x1, y1, x2, y2) in enumerate(pixel_boxes):
        masks[index, y1:y2, x1:x2] = True
    return masks


class BoxOnlyPromptTests(unittest.TestCase):
    def test_predict_is_called_with_box_only_and_multimask(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = solid_source(source)
            masks = full_size_masks((80, 100), [(30, 30, 60, 50)] * 3)
            predictor = FakeSamPredictor(masks, np.array([0.9, 0.95, 0.8]))
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(
                sam_request(source, [asset_entry("icon_001", bbox)]), temp / "output"
            )
            self.assertEqual("success", result["status"])
            self.assertEqual(1, len(predictor.predict_calls))
            call = predictor.predict_calls[0]
            self.assertIn("box", call)
            self.assertTrue(call["multimask_output"])
            self.assertNotIn("point_coords", call)
            self.assertNotIn("point_labels", call)
            self.assertEqual((36, 26, 64, 54), tuple(int(v) for v in np.asarray(call["box"])))

    def test_metadata_records_box_only_prompt_and_sam_model(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = solid_source(source)
            masks = full_size_masks((80, 100), [(30, 30, 60, 50)] * 3)
            predictor = FakeSamPredictor(masks, np.array([0.9, 0.95, 0.8]))
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(
                sam_request(source, [asset_entry("icon_001", bbox)]), temp / "output"
            )
            params = result["assets"][0]["mask_parameters"]
            self.assertEqual("sam1_vit_b", params["model"])
            self.assertEqual("vit_b", params["model_type"])
            self.assertEqual("box_only", params["prompt"])
            self.assertEqual("max_sam_score", params["winner_selection"])
            self.assertEqual([], extractor.validate_result(result))


class WinnerSelectionTests(unittest.TestCase):
    def test_winner_is_max_sam_score(self):
        scores = np.array([0.91, 0.99, 0.87])
        self.assertEqual(1, sam_backend.select_winner(scores))
        self.assertEqual(0, sam_backend.select_winner(np.array([0.99, 0.10])))

    def test_metadata_winner_matches_max_score(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = solid_source(source)
            # Candidate 2 has the largest mask; candidate 1 has the best
            # score. The frozen rule must pick the best score, not the
            # largest mask.
            masks = full_size_masks(
                (80, 100),
                [(30, 30, 60, 50), (32, 32, 58, 48), (28, 28, 64, 54)],
            )
            predictor = FakeSamPredictor(masks, np.array([0.90, 0.98, 0.70]))
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(
                sam_request(source, [asset_entry("icon_001", bbox)]), temp / "output"
            )
            params = result["assets"][0]["mask_parameters"]
            self.assertEqual(1, params["winner_index"])
            self.assertAlmostEqual(0.98, params["winner_sam_score"])
            self.assertEqual(3, params["candidate_count"])

    def test_no_candidates_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = solid_source(source)
            masks = np.zeros((0, 80, 100), dtype=bool)
            predictor = FakeSamPredictor(masks, np.zeros((0,)))
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(
                sam_request(source, [asset_entry("icon_001", bbox)]), temp / "output"
            )
            item = result["assets"][0]
            self.assertEqual("failed", item["status"])
            self.assertIn("no candidates", item["failure_reason"])


class EncodeOnceTests(unittest.TestCase):
    def test_set_image_called_exactly_once_for_multiple_assets(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            pixels = np.full((80, 100, 4), BACKGROUND, dtype=np.uint8)
            pixels[30:50, 40:60] = FOREGROUND
            pixels[60:70, 10:25] = FOREGROUND
            Image.fromarray(pixels, "RGBA").save(source)
            assets = [
                asset_entry("icon_001", {"x": 36, "y": 26, "width": 28, "height": 28}),
                asset_entry("icon_002", {"x": 6, "y": 56, "width": 23, "height": 18}),
            ]
            request = sam_request(source, assets)
            original_request = copy.deepcopy(request)

            def box_masks(box):
                x1, y1, x2, y2 = (int(v) for v in np.asarray(box))
                masks = full_size_masks(
                    (80, 100),
                    [(max(0, x1 - 4), max(0, y1 - 4), x2 + 4, y2 + 4)] * 3,
                )
                return masks, np.array([0.90, 0.95, 0.80])

            predictor = FakeSamPredictor(None, None, handler=box_masks)
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(request, temp / "output")
            self.assertEqual("success", result["status"])
            self.assertEqual(1, predictor.set_image_calls)
            self.assertEqual(2, len(predictor.predict_calls))
            self.assertEqual((80, 100, 3), predictor.encoded_shape)
            self.assertEqual(original_request, request)


class PostprocessUnitTests(unittest.TestCase):
    def test_close_3x3_repairs_one_pixel_gap(self):
        mask = np.zeros((9, 9), dtype=bool)
        mask[4, :4] = True
        mask[4, 5:] = True  # 1px vertical gap at x=4
        closed = sam_backend.close_3x3(mask)
        self.assertTrue(closed[4, 4])
        self.assertGreater(int(closed.sum()), int(mask.sum()))

    def test_close_3x3_keeps_stable_mask_pixels(self):
        mask = np.zeros((9, 9), dtype=bool)
        mask[2:7, 2:7] = True
        self.assertTrue(np.array_equal(mask, sam_backend.close_3x3(mask)))

    def test_components_use_8_connectivity(self):
        mask = np.zeros((6, 6), dtype=bool)
        mask[1, 1] = True
        mask[2, 2] = True  # diagonal neighbour: same component under 8-connectivity
        components, label_image = sam_backend.connected_components_8(mask)
        self.assertEqual(1, len(components))
        self.assertEqual(2, components[0]["area"])
        self.assertEqual(label_image[1, 1], label_image[2, 2])

    def test_components_split_without_8_connectivity(self):
        mask = np.zeros((6, 8), dtype=bool)
        mask[1, 1] = True
        mask[3, 3] = True  # separated by more than one diagonal step
        components, _ = sam_backend.connected_components_8(mask)
        self.assertEqual(2, len(components))

    def test_largest_component_is_kept(self):
        mask = np.zeros((10, 20), dtype=bool)
        mask[2:8, 2:12] = True   # largest
        mask[2:4, 15:18] = True  # small island
        filtered, stats = sam_backend.filter_components(mask)
        self.assertTrue(filtered[4, 6].all())
        self.assertGreaterEqual(stats["kept_component_count"], 1)
        self.assertIn(
            stats["largest_component_area"],
            [component["area"] for component in stats["components"]],
        )

    def test_component_at_or_above_8_percent_is_kept(self):
        mask = np.zeros((10, 40), dtype=bool)
        mask[2:8, 2:12] = True    # 60 px largest
        mask[2:8, 20:25] = True   # 30 px = 50% >= 8% -> kept
        filtered, stats = sam_backend.filter_components(mask)
        self.assertEqual(2, stats["component_count"])
        self.assertEqual(2, stats["kept_component_count"])
        self.assertEqual(0, stats["removed_component_count"])
        self.assertTrue(filtered[4, 22])

    def test_component_below_8_percent_is_removed(self):
        mask = np.zeros((10, 40), dtype=bool)
        mask[2:8, 2:12] = True    # 60 px largest
        mask[3, 30:32] = True     # 2 px < 8% of 60 (4.8) -> removed
        filtered, stats = sam_backend.filter_components(mask)
        self.assertEqual(2, stats["component_count"])
        self.assertEqual(1, stats["kept_component_count"])
        self.assertEqual(1, stats["removed_component_count"])
        self.assertFalse(filtered.any(axis=0)[30:32].any())
        self.assertEqual(60, int(filtered.sum()))

    def test_positive_point_hits_are_kept_even_when_small(self):
        mask = np.zeros((10, 40), dtype=bool)
        mask[2:8, 2:12] = True    # largest
        mask[3, 30:32] = True     # small island hit by a positive point
        filtered, stats = sam_backend.filter_components(
            mask, positive_points=[(30, 3)]
        )
        # Largest is kept by the area rule, the island is kept by its
        # positive-point hit, so nothing is removed.
        self.assertEqual(2, stats["component_count"])
        self.assertEqual(2, stats["kept_component_count"])
        self.assertEqual(0, stats["removed_component_count"])
        self.assertTrue(filtered[3, 30])

    def test_small_island_without_point_hit_is_removed(self):
        mask = np.zeros((10, 40), dtype=bool)
        mask[2:8, 2:12] = True    # largest
        mask[3, 30:32] = True     # 2 px island, no point hit
        filtered, stats = sam_backend.filter_components(mask)
        self.assertEqual(1, stats["kept_component_count"])
        self.assertEqual(1, stats["removed_component_count"])
        self.assertFalse(filtered[3, 30])
        self.assertTrue(filtered[4, 6])

    def test_empty_mask_yields_empty_result_and_stats(self):
        mask = np.zeros((8, 8), dtype=bool)
        filtered, stats = sam_backend.filter_components(mask)
        self.assertFalse(filtered.any())
        self.assertEqual(0, stats["component_count"])
        self.assertEqual(0, stats["kept_component_count"])


class SamExtractionTests(unittest.TestCase):
    def test_empty_mask_after_postprocess_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = solid_source(source)
            masks = full_size_masks((80, 100), [(0, 0, 0, 0)] * 3)
            predictor = FakeSamPredictor(masks, np.array([0.9, 0.8, 0.7]))
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(
                sam_request(source, [asset_entry("icon_001", bbox)]), temp / "output"
            )
            item = result["assets"][0]
            self.assertEqual("failed", item["status"])
            self.assertIn("empty", item["failure_reason"])
            self.assertIsNone(item["output_path"])

    def test_out_of_bounds_bbox_fails_without_prompting_sam(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            solid_source(source)
            masks = full_size_masks((80, 100), [(30, 30, 60, 50)] * 3)
            predictor = FakeSamPredictor(masks, np.array([0.9, 0.8, 0.7]))
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(
                sam_request(
                    source,
                    [asset_entry("icon_001", {"x": 90, "y": 70, "width": 20, "height": 20})],
                ),
                temp / "output",
            )
            item = result["assets"][0]
            self.assertEqual("failed", item["status"])
            self.assertIn("outside source-image bounds", item["failure_reason"])
            self.assertEqual(0, len(predictor.predict_calls))

    def test_source_rgb_preserved_and_alpha_from_postprocessed_mask(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            pixels = np.full((80, 100, 4), BACKGROUND, dtype=np.uint8)
            pixels[30:50, 40:60] = FOREGROUND
            # Source has partial alpha in one interior pixel.
            pixels[40, 50] = (200, 100, 50, 128)
            Image.fromarray(pixels, "RGBA").save(source)
            bbox = {"x": 36, "y": 26, "width": 28, "height": 28}

            masks = full_size_masks((80, 100), [(40, 30, 60, 50)] * 3)
            predictor = FakeSamPredictor(masks, np.array([0.9, 0.95, 0.8]))
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(
                sam_request(source, [asset_entry("icon_001", bbox)]), temp / "output"
            )
            item = result["assets"][0]
            self.assertEqual("success", item["status"])
            with Image.open(temp / "output" / item["output_path"]) as image:
                rgba = np.asarray(image)
            with Image.open(source) as image:
                source_rgba = np.asarray(image.convert("RGBA"))
            roi = item["extraction_roi"]
            expected_rgb = source_rgba[roi["y"]:roi["y"] + roi["height"],
                                       roi["x"]:roi["x"] + roi["width"], :3]

            self.assertTrue(np.array_equal(expected_rgb, rgba[:, :, :3]))
            # Alpha is exactly the postprocessed mask (opaque source pixels).
            mask_roi = masks[1][roi["y"]:roi["y"] + roi["height"],
                                roi["x"]:roi["x"] + roi["width"]]
            self.assertTrue(np.array_equal(mask_roi, rgba[:, :, 3] > 0))
            # Partial-alpha source pixel keeps its source alpha value.
            self.assertEqual(128, int(rgba[40 - roi["y"], 50 - roi["x"], 3]))

    def test_mask_metadata_records_postprocess_contract(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = solid_source(source)
            masks = full_size_masks((80, 100), [(30, 30, 60, 50)] * 3)
            predictor = FakeSamPredictor(masks, np.array([0.9, 0.95, 0.8]))
            patch_sam_loader(self, predictor)

            result = extractor.execute_request(
                sam_request(source, [asset_entry("icon_001", bbox)]), temp / "output"
            )
            item = result["assets"][0]
            params = item["mask_parameters"]
            self.assertEqual("3x3_ones", params["postprocess"]["close_kernel"])
            self.assertEqual(1, params["postprocess"]["close_iterations"])
            self.assertEqual(8, params["postprocess"]["connectivity"])
            self.assertEqual(0.08, params["postprocess"]["relative_component_threshold"])
            self.assertGreater(params["mask_area"], 0)
            self.assertGreaterEqual(params["component_count"], params["kept_component_count"])
            self.assertEqual("sam1_box_v0", item["mask_method"])
            self.assertIsNone(item["background_method"])
            self.assertEqual(
                {
                    "dilation_radius": 0,
                    "gaussian_blur_radius": 0.0,
                    "source_alpha_rule": "multiply",
                    "alpha_representation": "straight",
                },
                item["alpha_parameters"],
            )

    def test_direct_crop_works_under_sam_backend_without_sam_calls(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = solid_source(source)
            masks = full_size_masks((80, 100), [(30, 30, 60, 50)] * 3)
            predictor = FakeSamPredictor(masks, np.array([0.9, 0.95, 0.8]))
            patch_sam_loader(self, predictor)

            entry = asset_entry("button_001", bbox)
            entry["extraction_mode"] = "direct_crop"
            result = extractor.execute_request(
                sam_request(source, [entry]), temp / "output"
            )
            item = result["assets"][0]
            self.assertEqual("success", item["status"])
            self.assertIsNone(item["mask_path"])
            self.assertEqual(0, len(predictor.predict_calls))
            with Image.open(source) as image:
                expected = np.asarray(image.convert("RGBA"))[26:54, 36:64]
            with Image.open(temp / "output" / item["output_path"]) as image:
                self.assertTrue(np.array_equal(expected, np.asarray(image)))


class ConfigAndErrorTests(unittest.TestCase):
    def test_sam_defaults_are_merged_only_for_sam_backend(self):
        pillow_config = extractor.effective_config({"schema_version": "0.1", "source_image": "x", "assets": []})
        self.assertNotIn("sam_checkpoint", pillow_config)
        sam_config = extractor.effective_config(
            {"schema_version": "0.1", "source_image": "x", "assets": [],
             "config": {"backend": "sam1_vit_b"}}
        )
        self.assertEqual("vit_b", sam_config["sam_model_type"])
        self.assertEqual("auto", sam_config["device"])

    def test_missing_checkpoint_fails_with_diagnosable_error(self):
        with self.assertRaises(sam_backend.SamBackendError) as caught:
            sam_backend.load_sam_predictor("vit_b", "", "cpu")
        self.assertIn("checkpoint", str(caught.exception))

    def test_nonexistent_checkpoint_fails_with_path(self):
        with self.assertRaises(sam_backend.SamBackendError) as caught:
            sam_backend.load_sam_predictor("vit_b", "Z:/nope/missing.pth", "cpu")
        self.assertIn("not found", str(caught.exception))

    def test_unsupported_model_type_is_rejected(self):
        with self.assertRaises(sam_backend.SamBackendError) as caught:
            sam_backend.load_sam_predictor("vit_l", "whatever.pth", "cpu")
        self.assertIn("vit_b", str(caught.exception))

    def test_invalid_device_is_rejected(self):
        with self.assertRaises(sam_backend.SamBackendError) as caught:
            sam_backend.resolve_device("tpu")
        self.assertIn("auto", str(caught.exception))

    def test_cpu_device_resolves_without_torch(self):
        self.assertEqual(("cpu", False), sam_backend.resolve_device("cpu"))

    def test_cuda_requested_without_cuda_fails_explicitly(self):
        try:
            import torch
            has_cuda = torch.cuda.is_available()
        except ImportError:
            has_cuda = False
        if has_cuda:
            self.skipTest("CUDA is available on this machine")
        with self.assertRaises(sam_backend.SamBackendError) as caught:
            sam_backend.resolve_device("cuda")
        self.assertIn("CUDA is not available", str(caught.exception))

    def test_backend_error_does_not_silently_fallback(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            solid_source(source)
            request = sam_request(source, [asset_entry("icon_001", {"x": 36, "y": 26, "width": 28, "height": 28})])
            request["config"]["sam_checkpoint"] = str(temp / "missing.pth")

            with self.assertRaises(sam_backend.SamBackendError):
                extractor.execute_request(request, temp / "output")
            self.assertFalse((temp / "output" / "assets").exists())


class OptionalIntegrationTests(unittest.TestCase):
    """Only run with a real checkpoint via SAM_INTEGRATION_CHECKPOINT."""

    def setUp(self):
        checkpoint = os.environ.get("SAM_INTEGRATION_CHECKPOINT")
        if not checkpoint or not Path(checkpoint).is_file():
            self.skipTest("set SAM_INTEGRATION_CHECKPOINT to run the real-checkpoint integration test")
        self.checkpoint = checkpoint

    def test_real_checkpoint_loads_and_predicts(self):
        predictor, info = sam_backend.load_sam_predictor("vit_b", self.checkpoint, "auto")
        self.assertEqual("sam1_vit_b", info["model"])
        image = np.full((64, 64, 3), 255, dtype=np.uint8)
        image[20:44, 20:44] = (200, 30, 30)
        sam_backend.encode_source(predictor, image)
        masks, scores = sam_backend.predict_box(predictor, (16, 16, 48, 48))
        self.assertGreaterEqual(masks.shape[0], 1)
        self.assertGreater(float(scores.max()), 0.0)


if __name__ == "__main__":
    unittest.main()
