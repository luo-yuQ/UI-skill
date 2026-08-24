from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stage2_b.extraction_executor import ExtractionArtifact, ExtractionExecutor  # noqa: E402
from stage2_b.extraction_plan import (  # noqa: E402
    ExtractionPlanner,
    PlanningDecision,
    load_schema,
    validate_extraction_plan,
)
from stage2_b.quality_gate import ExtractionQualityGate  # noqa: E402


def make_asset_leaf(taxonomy: str = "icon") -> dict:
    return {
        "asset_id": "asset_001",
        "node_id": "node_010",
        "taxonomy": taxonomy,
        "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
        "source_crop": "runs/example/node-crop.png",
    }


class FixedPolicy:
    name = "contract_test_policy"

    def __init__(self, mode: str, backend: str) -> None:
        self.mode = mode
        self.backend = backend

    def decide(self, asset_leaf) -> PlanningDecision:
        return PlanningDecision(
            extraction_mode=self.mode,
            backend=self.backend,
            confidence=0.8,
            reason="Deterministic contract-test evidence.",
        )


class ExtractionContractTests(unittest.TestCase):
    def test_schema_is_valid_draft_2020_12(self):
        schema = load_schema()
        self.assertEqual(
            "https://json-schema.org/draft/2020-12/schema",
            schema["$schema"],
        )
        Draft202012Validator.check_schema(schema)

    def test_asset_leaf_planner_produces_valid_extraction_plan(self):
        plan = ExtractionPlanner().plan(make_asset_leaf())

        self.assertEqual([], validate_extraction_plan(plan))
        self.assertEqual("asset_001", plan["asset_id"])
        self.assertEqual("repair_required", plan["extraction_mode"])
        self.assertEqual("unknown", plan["backend"])

    def test_planner_does_not_modify_bbox(self):
        asset_leaf = make_asset_leaf()
        original = deepcopy(asset_leaf)

        plan = ExtractionPlanner().plan(asset_leaf)

        self.assertEqual(original, asset_leaf)
        self.assertEqual(asset_leaf["bbox"], plan["metadata"]["input_bbox"])
        self.assertIsNot(asset_leaf["bbox"], plan["metadata"]["input_bbox"])

    def test_all_three_extraction_modes_are_schema_valid(self):
        cases = (
            ("direct_crop", "direct"),
            ("foreground_extract", "color_distance"),
            ("repair_required", "unknown"),
        )
        for mode, backend in cases:
            with self.subTest(mode=mode, backend=backend):
                plan = ExtractionPlanner(FixedPolicy(mode, backend)).plan(
                    make_asset_leaf()
                )
                self.assertEqual([], validate_extraction_plan(plan))
                self.assertEqual(mode, plan["extraction_mode"])

    def test_default_policy_does_not_branch_on_taxonomy(self):
        icon_plan = ExtractionPlanner().plan(make_asset_leaf("icon"))
        panel_plan = ExtractionPlanner().plan(make_asset_leaf("panel"))

        self.assertEqual(icon_plan["extraction_mode"], panel_plan["extraction_mode"])
        self.assertEqual(icon_plan["backend"], panel_plan["backend"])

    def test_executor_rejects_changed_bbox_before_backend_execution(self):
        asset_leaf = make_asset_leaf()
        plan = ExtractionPlanner(FixedPolicy("direct_crop", "direct")).plan(
            asset_leaf
        )
        plan["metadata"]["input_bbox"]["width"] += 1

        with self.assertRaisesRegex(ValueError, "cannot modify"):
            ExtractionExecutor().execute(plan, asset_leaf)

    def test_quality_gate_rejects_empty_output(self):
        plan = ExtractionPlanner(FixedPolicy("direct_crop", "direct")).plan(
            make_asset_leaf()
        )
        artifact = ExtractionArtifact(success=True, png_bytes=b"", width=0, height=0)

        result = ExtractionQualityGate().evaluate(plan, artifact)

        self.assertFalse(result.passed)
        self.assertIn("empty_output", result.issues)

    def test_quality_gate_rejects_empty_mask_without_visual_keywords(self):
        plan = ExtractionPlanner(
            FixedPolicy("foreground_extract", "grabcut")
        ).plan(make_asset_leaf())
        artifact = ExtractionArtifact(
            success=True,
            png_bytes=b"png",
            width=30,
            height=40,
            foreground_pixels=0,
            background_pixels=1200,
            total_mask_pixels=1200,
        )

        result = ExtractionQualityGate().evaluate(plan, artifact)

        self.assertFalse(result.passed)
        self.assertIn("empty_mask", result.issues)
        self.assertIn("foreground_ratio_abnormal", result.issues)


if __name__ == "__main__":
    unittest.main()
