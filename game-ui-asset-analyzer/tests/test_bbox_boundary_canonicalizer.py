from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bbox_boundary_canonicalizer import (  # noqa: E402
    BBOX_BOUNDARY_TOLERANCE_PX,
    canonicalize_strategy_bboxes,
)


class BBoxBoundaryCanonicalizerTests(unittest.TestCase):
    @staticmethod
    def document(bbox: dict[str, int]) -> dict:
        return {"children": [{"bbox": copy.deepcopy(bbox)}]}

    def canonicalize(
        self,
        bbox: dict[str, int],
        *,
        image_size: tuple[int, int] = (1024, 78),
    ) -> tuple[dict, list[dict]]:
        document = self.document(bbox)
        diagnostics = canonicalize_strategy_bboxes(
            document,
            strategy="structural_split",
            image_size=image_size,
        )
        return document["children"][0]["bbox"], diagnostics

    def test_tolerance_is_frozen_at_four_pixels(self):
        self.assertEqual(4, BBOX_BOUNDARY_TOLERANCE_PX)

    def test_bottom_plus_one_is_clamped(self):
        bbox, diagnostics = self.canonicalize(
            {"x": 10, "y": 200, "width": 100, "height": 56},
            image_size=(1024, 255),
        )
        self.assertEqual(
            {"x": 10, "y": 200, "width": 100, "height": 55}, bbox
        )
        self.assertEqual(-1, diagnostics[0]["adjustments"]["bottom"]["delta_px"])

    def test_bottom_plus_two_is_clamped(self):
        bbox, diagnostics = self.canonicalize(
            {"x": 10, "y": 20, "width": 100, "height": 60}
        )
        self.assertEqual(
            {"x": 10, "y": 20, "width": 100, "height": 58}, bbox
        )
        self.assertEqual(-2, diagnostics[0]["adjustments"]["bottom"]["delta_px"])

    def test_exactly_plus_four_is_clamped(self):
        bbox, diagnostics = self.canonicalize(
            {"x": 900, "y": 10, "width": 128, "height": 20}
        )
        self.assertEqual(
            {"x": 900, "y": 10, "width": 124, "height": 20}, bbox
        )
        self.assertEqual(-4, diagnostics[0]["adjustments"]["right"]["delta_px"])

    def test_plus_five_is_not_repaired(self):
        raw = {"x": 10, "y": 20, "width": 100, "height": 63}
        bbox, diagnostics = self.canonicalize(raw)
        self.assertEqual(raw, bbox)
        self.assertEqual([], diagnostics)

    def test_one_tolerable_and_one_excessive_edge_are_not_partially_repaired(self):
        raw = {"x": -2, "y": 20, "width": 100, "height": 63}
        bbox, diagnostics = self.canonicalize(raw)
        self.assertEqual(raw, bbox)
        self.assertEqual([], diagnostics)

    def test_left_minus_one_and_minus_four_are_clamped(self):
        for x in (-1, -4):
            with self.subTest(x=x):
                raw = {"x": x, "y": 10, "width": 20, "height": 20}
                bbox, diagnostics = self.canonicalize(raw)
                self.assertEqual(0, bbox["x"])
                self.assertEqual(20 + x, bbox["width"])
                self.assertEqual(-x, diagnostics[0]["adjustments"]["left"]["delta_px"])

    def test_left_minus_five_is_not_repaired(self):
        raw = {"x": -5, "y": 10, "width": 20, "height": 20}
        bbox, diagnostics = self.canonicalize(raw)
        self.assertEqual(raw, bbox)
        self.assertEqual([], diagnostics)

    def test_multi_edge_clamps_atomically(self):
        bbox, diagnostics = self.canonicalize(
            {"x": -2, "y": 20, "width": 1020, "height": 60}
        )
        self.assertEqual(
            {"x": 0, "y": 20, "width": 1018, "height": 58}, bbox
        )
        self.assertEqual(
            {"left", "bottom"}, set(diagnostics[0]["adjustments"])
        )

    def test_fully_in_bounds_bbox_is_value_equivalent_no_op(self):
        raw = {"x": 10, "y": 20, "width": 100, "height": 40}
        bbox, diagnostics = self.canonicalize(raw)
        self.assertEqual(raw, bbox)
        self.assertEqual([], diagnostics)

    def test_bbox_without_positive_intersection_is_not_repaired(self):
        raw = {"x": -4, "y": 10, "width": 2, "height": 20}
        bbox, diagnostics = self.canonicalize(raw)
        self.assertEqual(raw, bbox)
        self.assertEqual([], diagnostics)

    def test_non_integer_or_non_positive_bbox_is_left_for_frozen_validator(self):
        for raw in (
            {"x": -1.0, "y": 10, "width": 20, "height": 20},
            {"x": -1, "y": 10, "width": 0, "height": 20},
        ):
            with self.subTest(raw=raw):
                bbox, diagnostics = self.canonicalize(raw)
                self.assertEqual(raw, bbox)
                self.assertEqual([], diagnostics)


if __name__ == "__main__":
    unittest.main()
