from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from production_visual_adapter import (  # noqa: E402
    ProductionVisualAdapter,
    StrategySchemaValidationError,
)
import validate_structural_split  # noqa: E402


class MockVLMClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = copy.deepcopy(response)

    def infer_json(self, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(self.response)


def structural(children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "no_useful_structural_split": False,
        "children": children,
        "reason": "Stable structural regions.",
    }


def structural_child(child_id: str, bbox: dict[str, int]) -> dict[str, Any]:
    return {
        "id": child_id,
        "label": child_id,
        "bbox": bbox,
        "confidence": 0.95,
    }


def expand(bbox: dict[str, int]) -> dict[str, Any]:
    return {
        "instance_type": "slot",
        "repeat_count": 1,
        "instances": [
            {
                "id": "instance_001",
                "bbox": bbox,
                "partial_instance": False,
                "confidence": 0.95,
            }
        ],
        "reason": "One visible peer instance.",
    }


def semantic(bbox: dict[str, int], *, height: int) -> dict[str, Any]:
    return {
        "node_id": "current",
        "node_role": "component_instance",
        "task": "semantic_decompose",
        "bbox_constraint": "completeness",
        "analysis_image_size": {"width": 1024, "height": height},
        "decision": "decompose",
        "children": [
            {
                "id": "asset_001",
                "label": "asset",
                "taxonomy": "illustration",
                "bbox": bbox,
                "partial": False,
                "confidence": 0.95,
            }
        ],
        "reason": "One direct visual asset.",
    }


class BBoxBoundaryToleranceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)

    def image(self, name: str, height: int) -> Path:
        path = self.base / name
        Image.new("RGB", (1024, height), "navy").save(path)
        return path

    @staticmethod
    def adapter(response: dict[str, Any]) -> ProductionVisualAdapter:
        return ProductionVisualAdapter(MockVLMClient(response))

    def diagnostic(self, image: Path, strategy: str) -> dict[str, Any]:
        path = image.parent / f"{strategy}-bbox-boundary-canonicalization.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_real_regression_255_to_256_is_canonicalized_before_semantic_validator(self):
        image = self.image("case-a.png", 255)
        raw_bbox = {"x": 100, "y": 200, "width": 300, "height": 56}
        result = self.adapter(semantic(raw_bbox, height=255)).semantic_decompose(image)

        self.assertEqual(
            {"x": 100, "y": 200, "width": 300, "height": 55},
            result["children"][0]["bbox"],
        )
        diagnostic = self.diagnostic(image, "semantic_decompose")
        record = diagnostic["canonicalizations"][0]
        self.assertEqual(raw_bbox, record["raw_bbox"])
        self.assertEqual(result["children"][0]["bbox"], record["canonical_bbox"])
        self.assertEqual(-1, record["adjustments"]["bottom"]["delta_px"])

    def test_real_regression_six_78_to_80_children_all_pass_frozen_validator(self):
        image = self.image("case-b.png", 78)
        children = [
            structural_child(
                f"child_{index + 1:03d}",
                {"x": index * 160, "y": 20, "width": 140, "height": 60},
            )
            for index in range(6)
        ]
        result = self.adapter(structural(children)).structural_split(image)

        self.assertEqual(6, len(result["children"]))
        self.assertTrue(
            all(child["bbox"]["y"] + child["bbox"]["height"] == 78 for child in result["children"])
        )
        self.assertEqual([], validate_structural_split.validate_document(result, image))
        diagnostic = self.diagnostic(image, "structural_split")
        self.assertEqual(6, len(diagnostic["canonicalizations"]))
        self.assertTrue(
            all(
                record["adjustments"]["bottom"]["delta_px"] == -2
                for record in diagnostic["canonicalizations"]
            )
        )

    def test_exactly_plus_four_is_canonicalized_for_expand_instances(self):
        image = self.image("plus-four.png", 78)
        result = self.adapter(
            expand({"x": 900, "y": 10, "width": 128, "height": 20})
        ).expand_instances(image)
        self.assertEqual(
            {"x": 900, "y": 10, "width": 124, "height": 20},
            result["instances"][0]["bbox"],
        )

    def test_plus_five_remains_a_strategy_schema_validation_error(self):
        image = self.image("plus-five.png", 78)
        response = semantic(
            {"x": 10, "y": 20, "width": 100, "height": 63}, height=78
        )
        with self.assertRaisesRegex(
            StrategySchemaValidationError, "strategy_schema_validation_error"
        ):
            self.adapter(response).semantic_decompose(image)

    def test_horizontal_relative_tolerance_is_canonicalized(self):
        for x in (-1, -4, -5, -16):
            with self.subTest(x=x):
                image = self.image(f"left-{abs(x)}.png", 78)
                result = self.adapter(
                    expand({"x": x, "y": 10, "width": 20, "height": 20})
                ).expand_instances(image)
                self.assertEqual(0, result["instances"][0]["bbox"]["x"])

    def test_horizontal_cap_plus_one_remains_a_strategy_schema_validation_error(self):
        image = self.image("left-seventeen.png", 78)
        with self.assertRaisesRegex(
            StrategySchemaValidationError, "strategy_schema_validation_error"
        ):
            self.adapter(
                expand({"x": -17, "y": 10, "width": 20, "height": 20})
            ).expand_instances(image)

    def test_lucky_wheel_case_a_305_to_320_passes_structural_validator(self):
        image = self.image("lucky-wheel-case-a.png", 305)
        raw_bbox = {"x": 100, "y": 100, "width": 300, "height": 220}
        result = self.adapter(
            structural([structural_child("child_003", raw_bbox)])
        ).structural_split(image)

        canonical_bbox = {"x": 100, "y": 100, "width": 300, "height": 205}
        self.assertEqual(canonical_bbox, result["children"][0]["bbox"])
        self.assertEqual([], validate_structural_split.validate_document(result, image))
        diagnostic = self.diagnostic(image, "structural_split")
        self.assertEqual("0.2", diagnostic["diagnostic_version"])
        self.assertEqual("bbox-boundary-tolerance-v0.2", diagnostic["policy"])
        record = diagnostic["canonicalizations"][0]
        self.assertEqual(raw_bbox, record["raw_bbox"])
        self.assertEqual(canonical_bbox, record["canonical_bbox"])
        self.assertEqual(15, record["adjustments"]["bottom"]["overflow_px"])
        self.assertEqual(
            16, record["adjustments"]["bottom"]["edge_tolerance_px"]
        )

    def test_lucky_wheel_case_b_195_to_200_passes_structural_validator(self):
        image = self.image("lucky-wheel-case-b.png", 195)
        raw_bbox = {"x": 100, "y": 100, "width": 300, "height": 100}
        result = self.adapter(
            structural([structural_child("child_002", raw_bbox)])
        ).structural_split(image)

        canonical_bbox = {"x": 100, "y": 100, "width": 300, "height": 95}
        self.assertEqual(canonical_bbox, result["children"][0]["bbox"])
        self.assertEqual([], validate_structural_split.validate_document(result, image))
        record = self.diagnostic(image, "structural_split")["canonicalizations"][0]
        self.assertEqual(raw_bbox, record["raw_bbox"])
        self.assertEqual(canonical_bbox, record["canonical_bbox"])
        self.assertEqual(5, record["adjustments"]["bottom"]["overflow_px"])
        self.assertEqual(
            10, record["adjustments"]["bottom"]["edge_tolerance_px"]
        )

    def test_multi_edge_canonicalization_passes_semantic_validator(self):
        image = self.image("multi-edge.png", 78)
        result = self.adapter(
            semantic(
                {"x": -2, "y": 20, "width": 1020, "height": 60}, height=78
            )
        ).semantic_decompose(image)
        self.assertEqual(
            {"x": 0, "y": 20, "width": 1018, "height": 58},
            result["children"][0]["bbox"],
        )

    def test_no_op_preserves_legal_bbox_and_writes_no_diagnostic(self):
        image = self.image("no-op.png", 78)
        raw_bbox = {"x": 10, "y": 20, "width": 100, "height": 40}
        result = self.adapter(structural([structural_child("child", raw_bbox)])).structural_split(image)
        self.assertEqual(raw_bbox, result["children"][0]["bbox"])
        self.assertFalse(
            (image.parent / "structural_split-bbox-boundary-canonicalization.json").exists()
        )

    def test_no_positive_intersection_remains_a_validation_error(self):
        image = self.image("no-intersection.png", 78)
        response = structural(
            [structural_child("outside", {"x": -4, "y": 10, "width": 2, "height": 20})]
        )
        with self.assertRaisesRegex(
            StrategySchemaValidationError, "strategy_schema_validation_error"
        ):
            self.adapter(response).structural_split(image)

    def test_router_does_not_participate_in_bbox_canonicalization(self):
        image = self.image("router.png", 78)
        result = self.adapter(
            {
                "node_role": "asset",
                "confidence": 0.95,
                "reason": "One coherent visual asset.",
            }
        ).route(image)
        self.assertEqual("asset", result["node_role"])
        self.assertEqual([], list(image.parent.glob("router-bbox-boundary-*.json")))


if __name__ == "__main__":
    unittest.main()
