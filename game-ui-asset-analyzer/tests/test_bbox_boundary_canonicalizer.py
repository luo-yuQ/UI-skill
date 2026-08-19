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
    BBOX_BOUNDARY_TOLERANCE_CAP_PX,
    BBOX_BOUNDARY_TOLERANCE_FLOOR_PX,
    BBOX_BOUNDARY_TOLERANCE_PX,
    BBOX_BOUNDARY_TOLERANCE_RATIO,
    BBOX_BOUNDARY_TOLERANCE_VERSION,
    _compute_edge_tolerance,
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

    def test_v02_tolerance_constants_and_formula(self):
        self.assertEqual("0.2", BBOX_BOUNDARY_TOLERANCE_VERSION)
        self.assertEqual(4, BBOX_BOUNDARY_TOLERANCE_PX)
        self.assertEqual(4, BBOX_BOUNDARY_TOLERANCE_FLOOR_PX)
        self.assertEqual(16, BBOX_BOUNDARY_TOLERANCE_CAP_PX)
        self.assertEqual(0.05, BBOX_BOUNDARY_TOLERANCE_RATIO)
        self.assertEqual(4, _compute_edge_tolerance(40))
        self.assertEqual(4, _compute_edge_tolerance(78))
        self.assertEqual(10, _compute_edge_tolerance(195))
        self.assertEqual(16, _compute_edge_tolerance(305))
        self.assertEqual(16, _compute_edge_tolerance(1000))

    def test_invalid_edge_dimension_is_rejected(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "edge_dimension"
            ):
                _compute_edge_tolerance(value)  # type: ignore[arg-type]

    def test_bottom_plus_one_is_clamped(self):
        bbox, diagnostics = self.canonicalize(
            {"x": 10, "y": 200, "width": 100, "height": 56},
            image_size=(1024, 255),
        )
        self.assertEqual(
            {"x": 10, "y": 200, "width": 100, "height": 55}, bbox
        )
        self.assertEqual(-1, diagnostics[0]["adjustments"]["bottom"]["delta_px"])
        self.assertEqual(1, diagnostics[0]["adjustments"]["bottom"]["overflow_px"])
        self.assertEqual(
            13, diagnostics[0]["adjustments"]["bottom"]["edge_tolerance_px"]
        )

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

    def test_horizontal_cap_allows_left_minus_one_through_minus_sixteen(self):
        for x in (-1, -4, -5, -16):
            with self.subTest(x=x):
                raw = {"x": x, "y": 10, "width": 20, "height": 20}
                bbox, diagnostics = self.canonicalize(raw)
                self.assertEqual(0, bbox["x"])
                self.assertEqual(20 + x, bbox["width"])
                self.assertEqual(-x, diagnostics[0]["adjustments"]["left"]["delta_px"])

    def test_left_minus_seventeen_is_not_repaired(self):
        raw = {"x": -17, "y": 10, "width": 20, "height": 20}
        bbox, diagnostics = self.canonicalize(raw)
        self.assertEqual(raw, bbox)
        self.assertEqual([], diagnostics)

    def test_lucky_wheel_height_305_bottom_320_is_clamped(self):
        bbox, diagnostics = self.canonicalize(
            {"x": 10, "y": 200, "width": 100, "height": 120},
            image_size=(1024, 305),
        )
        self.assertEqual(
            {"x": 10, "y": 200, "width": 100, "height": 105}, bbox
        )
        adjustment = diagnostics[0]["adjustments"]["bottom"]
        self.assertEqual(15, adjustment["overflow_px"])
        self.assertEqual(16, adjustment["edge_tolerance_px"])

    def test_lucky_wheel_height_195_bottom_200_is_clamped(self):
        bbox, diagnostics = self.canonicalize(
            {"x": 10, "y": 100, "width": 100, "height": 100},
            image_size=(1024, 195),
        )
        self.assertEqual(
            {"x": 10, "y": 100, "width": 100, "height": 95}, bbox
        )
        adjustment = diagnostics[0]["adjustments"]["bottom"]
        self.assertEqual(5, adjustment["overflow_px"])
        self.assertEqual(10, adjustment["edge_tolerance_px"])

    def test_lucky_wheel_vertical_tolerance_plus_one_remains_unrepaired(self):
        for image_height, raw_bottom in ((305, 322), (195, 212)):
            with self.subTest(image_height=image_height, raw_bottom=raw_bottom):
                raw = {
                    "x": 10,
                    "y": 100,
                    "width": 100,
                    "height": raw_bottom - 100,
                }
                bbox, diagnostics = self.canonicalize(
                    raw, image_size=(1024, image_height)
                )
                self.assertEqual(raw, bbox)
                self.assertEqual([], diagnostics)

    def test_cap_accepts_plus_sixteen_and_rejects_plus_seventeen(self):
        accepted, diagnostics = self.canonicalize(
            {"x": 10, "y": 900, "width": 100, "height": 116},
            image_size=(1024, 1000),
        )
        self.assertEqual(
            {"x": 10, "y": 900, "width": 100, "height": 100}, accepted
        )
        self.assertEqual(16, diagnostics[0]["adjustments"]["bottom"]["overflow_px"])

        raw = {"x": 10, "y": 900, "width": 100, "height": 117}
        rejected, diagnostics = self.canonicalize(raw, image_size=(1024, 1000))
        self.assertEqual(raw, rejected)
        self.assertEqual([], diagnostics)

    def test_floor_accepts_plus_four_and_rejects_plus_five(self):
        accepted, diagnostics = self.canonicalize(
            {"x": 10, "y": 10, "width": 100, "height": 34},
            image_size=(200, 40),
        )
        self.assertEqual(
            {"x": 10, "y": 10, "width": 100, "height": 30}, accepted
        )
        self.assertEqual(4, diagnostics[0]["adjustments"]["bottom"]["edge_tolerance_px"])

        raw = {"x": 10, "y": 10, "width": 100, "height": 35}
        rejected, diagnostics = self.canonicalize(raw, image_size=(200, 40))
        self.assertEqual(raw, rejected)
        self.assertEqual([], diagnostics)

    def test_horizontal_width_200_accepts_plus_ten_and_rejects_plus_eleven(self):
        accepted, diagnostics = self.canonicalize(
            {"x": 100, "y": 10, "width": 110, "height": 20},
            image_size=(200, 195),
        )
        self.assertEqual(
            {"x": 100, "y": 10, "width": 100, "height": 20}, accepted
        )
        self.assertEqual(10, diagnostics[0]["adjustments"]["right"]["edge_tolerance_px"])

        raw = {"x": 100, "y": 10, "width": 111, "height": 20}
        rejected, diagnostics = self.canonicalize(raw, image_size=(200, 195))
        self.assertEqual(raw, rejected)
        self.assertEqual([], diagnostics)

    def test_relative_multi_edge_tolerances_are_applied_atomically(self):
        bbox, diagnostics = self.canonicalize(
            {"x": -5, "y": 100, "width": 100, "height": 100},
            image_size=(200, 195),
        )
        self.assertEqual(
            {"x": 0, "y": 100, "width": 95, "height": 95}, bbox
        )
        record = diagnostics[0]
        self.assertEqual({"horizontal": 10, "vertical": 10}, record["edge_tolerance_px"])
        self.assertEqual({"left", "bottom"}, set(record["adjustments"]))

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
