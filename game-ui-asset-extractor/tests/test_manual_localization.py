"""Contract tests for human secondary localization v0.1.

Hermetic, like ``test_sam_backend.py``: the SamPredictor is faked, so the
suite verifies orchestration contracts only (encode-once, prompt shapes,
frozen winner/postprocess reuse, deterministic subtraction, binary-alpha
RGBA). The real-checkpoint behavior was validated end-to-end by experiments
002-004 and the ``20260908_manual_localization_v01_smoke_001`` run.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (str(SCRIPTS),):
    if path not in sys.path:
        sys.path.insert(0, path)

import manual_localization as ml  # noqa: E402
import sam_backend  # noqa: E402


WIDTH = 80
HEIGHT = 100

TARGET_BBOX = {"x": 36, "y": 26, "width": 28, "height": 28}
TARGET_XYXY = (36, 26, 64, 54)
OCCLUDER_BBOX = {"x": 44, "y": 34, "width": 12, "height": 12}
OCCLUDER_XYXY = (44, 34, 56, 46)


class FakeSamPredictor:
    """Records orchestration calls; returns handler-scripted masks/scores."""

    def __init__(self, handler):
        self.handler = handler
        self.set_image_calls = 0
        self.predict_calls: list[dict] = []

    def set_image(self, image: np.ndarray) -> None:
        self.set_image_calls += 1

    def predict(self, **kwargs):
        self.predict_calls.append(kwargs)
        masks, scores = self.handler(kwargs)
        count = masks.shape[0]
        return masks, scores, np.zeros((count, 256, 256), dtype=np.float32)


def patch_sam_loader(testcase, predictor: FakeSamPredictor) -> None:
    load_info = {
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


def rectangle_mask(shape: tuple[int, int], x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    mask[y1:y2, x1:x2] = True
    return mask


def sector_masks(shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Target candidate: solid rect across the whole bbox (occluder included)."""

    candidate = rectangle_mask(shape, 38, 28, 62, 52)
    masks = np.stack([candidate] * 3)
    return masks, np.array([0.90, 0.98, 0.80])


def card_masks(shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    candidate = rectangle_mask(shape, 44, 34, 56, 46)
    masks = np.stack([candidate] * 3)
    return masks, np.array([0.95, 0.93, 0.91])


def default_handler(kwargs):
    box = tuple(int(v) for v in np.asarray(kwargs["box"]))
    if box == OCCLUDER_XYXY:
        return card_masks((HEIGHT, WIDTH))
    return sector_masks((HEIGHT, WIDTH))


def write_source(path: Path) -> None:
    pixels = np.full((HEIGHT, WIDTH, 3), (14, 22, 38), dtype=np.uint8)
    Image.fromarray(pixels, "RGB").save(path)


def base_request(source: Path, **overrides) -> dict:
    request = {
        "schema_version": "0.1",
        "localization_version": "human-secondary-localization-v0.1",
        "source_image": str(source),
        "config": {"backend": "sam1_vit_b", "sam_checkpoint": "fake.pth"},
        "target": {"bbox_source": dict(TARGET_BBOX)},
        "occluders": [
            {"occluder_id": "blue_card", "bbox_source": dict(OCCLUDER_BBOX)}
        ],
    }
    request.update(overrides)
    return request


class ComputeVisibleMaskTests(unittest.TestCase):
    def test_zero_occluders_returns_target(self):
        target = rectangle_mask((HEIGHT, WIDTH), 10, 10, 20, 20)
        np.testing.assert_array_equal(target, ml.compute_visible_mask(target, []))

    def test_union_of_occluders_is_subtracted(self):
        target = rectangle_mask((HEIGHT, WIDTH), 10, 10, 30, 30)
        first = rectangle_mask((HEIGHT, WIDTH), 12, 12, 16, 16)
        second = rectangle_mask((HEIGHT, WIDTH), 14, 14, 20, 20)
        visible = ml.compute_visible_mask(target, [first, second])
        expected = target & ~(first | second)
        np.testing.assert_array_equal(expected, visible)
        # union(first, second) = 16 + 36 - 4 overlap = 48 px, all inside target
        self.assertEqual(352, int(visible.sum()))

    def test_shape_mismatch_fails_explicitly(self):
        target = rectangle_mask((HEIGHT, WIDTH), 10, 10, 20, 20)
        with self.assertRaises(ml.ManualLocalizationError):
            ml.compute_visible_mask(target, [rectangle_mask((5, 5), 0, 0, 2, 2)])


class BinaryAlphaTests(unittest.TestCase):
    def test_rgba_uses_binary_alpha_and_untouched_rgb(self):
        source = np.arange(12 * 10 * 3, dtype=np.uint8).reshape(12, 10, 3)
        mask = rectangle_mask((12, 10), 2, 3, 7, 8)
        rgba = ml.build_rgba(source, mask)
        self.assertEqual((12, 10, 4), rgba.shape)
        np.testing.assert_array_equal(source, rgba[..., :3])
        self.assertEqual({0, 255}, set(np.unique(rgba[..., 3])))
        np.testing.assert_array_equal(mask.astype(np.uint8) * 255, rgba[..., 3])

    def test_rgba_shape_mismatch_fails_explicitly(self):
        source = np.zeros((12, 10, 3), dtype=np.uint8)
        with self.assertRaises(ml.ManualLocalizationError):
            ml.build_rgba(source, rectangle_mask((5, 5), 0, 0, 2, 2))

    def test_checkerboard_composite_preserves_opaque_pixels(self):
        source = np.full((12, 10, 3), (250, 120, 30), dtype=np.uint8)
        mask = rectangle_mask((12, 10), 2, 3, 7, 8)
        rgba = ml.build_rgba(source, mask)
        composite = ml.checkerboard_composite(rgba, square=4)
        np.testing.assert_array_equal(source[3, 4], composite[3, 4])
        transparent = np.argwhere(~mask)
        yy, xx = transparent[0]
        light = ((yy // 4 + xx // 4) % 2) == 0
        expected = 255 if light else 204
        self.assertTrue((composite[yy, xx] == expected).all())


class RequestValidationTests(unittest.TestCase):
    def test_out_of_bounds_target_bbox_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "source.png"
            write_source(source)
            request = base_request(source)
            request["target"]["bbox_source"]["x"] = 90
            with self.assertRaises(ml.ManualLocalizationError):
                ml.execute_request(request, temp / "out")

    def test_duplicate_occluder_ids_fail(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "source.png"
            write_source(source)
            request = base_request(source)
            request["occluders"].append(
                {"occluder_id": "blue_card", "bbox_source": dict(OCCLUDER_BBOX)}
            )
            with self.assertRaises(ml.ManualLocalizationError):
                ml.execute_request(request, temp / "out")

    def test_schema_invalid_request_fails_at_load(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            request_path = temp / "request.json"
            request_path.write_text(json.dumps({"schema_version": "0.1"}), encoding="utf-8")
            with self.assertRaises(ml.ManualLocalizationError):
                ml.load_request(request_path)


class OrchestrationTests(unittest.TestCase):
    def test_execute_request_contract(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "source.png"
            write_source(source)
            predictor = FakeSamPredictor(default_handler)
            patch_sam_loader(self, predictor)

            result = ml.execute_request(base_request(source), temp / "out")

            self.assertEqual(1, predictor.set_image_calls)
            self.assertEqual(2, len(predictor.predict_calls))
            box_only_call = predictor.predict_calls[0]
            self.assertNotIn("point_coords", box_only_call)
            self.assertNotIn("point_labels", box_only_call)
            self.assertEqual(TARGET_XYXY, tuple(int(v) for v in np.asarray(box_only_call["box"])))
            occluder_call = predictor.predict_calls[1]
            self.assertEqual(OCCLUDER_XYXY, tuple(int(v) for v in np.asarray(occluder_call["box"])))

            self.assertEqual("success", result["status"])
            self.assertEqual("human-secondary-localization-v0.1", result["localization_version"])
            self.assertEqual(
                {"target_mask": 576, "occluder_union_removed": 144, "visible_target_mask": 432},
                result["areas"],
            )
            self.assertEqual("binary_mask_0_255", result["alpha_rule"])

            visible = np.asarray(Image.open(temp / "out" / "visible-target-mask.png"))
            self.assertEqual({0, 255}, set(np.unique(visible)))
            self.assertEqual(432, int((visible == 255).sum()))
            self.assertFalse(visible[40, 50])  # occluder interior
            self.assertTrue(visible[30, 40])  # target ring
            self.assertFalse(visible[5, 5])  # outside target

            rgba = np.asarray(Image.open(temp / "out" / "visible-target-rgba.png"))
            self.assertEqual((HEIGHT, WIDTH, 4), rgba.shape)
            self.assertEqual({0, 255}, set(np.unique(rgba[..., 3])))

            for filename in (
                "target-mask.png",
                "occluder-blue_card-mask.png",
                "visible-target-mask.png",
                "visible-target-rgba.png",
                "visible-target-on-checkerboard.png",
                "result.json",
            ):
                self.assertTrue((temp / "out" / filename).is_file(), filename)

    def test_target_points_are_forwarded_with_binary_labels(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "source.png"
            write_source(source)
            predictor = FakeSamPredictor(default_handler)
            patch_sam_loader(self, predictor)

            request = base_request(source)
            request["target"]["positive_points_source"] = [[40, 30]]
            request["target"]["negative_points_source"] = [[10, 10]]
            ml.execute_request(request, temp / "out")

            target_call = predictor.predict_calls[0]
            np.testing.assert_array_equal(
                np.array([[40, 30], [10, 10]], dtype=np.float32),
                target_call["point_coords"],
            )
            self.assertEqual([1, 0], [int(v) for v in target_call["point_labels"]])
            self.assertTrue(target_call["multimask_output"])
            # The occluder prompt must stay box-only even when the target uses points.
            self.assertNotIn("point_coords", predictor.predict_calls[1])

    def test_missing_checkpoint_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "source.png"
            write_source(source)
            request = base_request(source)
            del request["config"]["sam_checkpoint"]
            with self.assertRaises(ml.ManualLocalizationError):
                ml.execute_request(request, temp / "out")


if __name__ == "__main__":
    unittest.main()
