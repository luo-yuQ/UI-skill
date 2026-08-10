from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_EXAMPLE = ROOT / "references" / "examples" / "example-ui-compose-input.json"
PLAN_EXAMPLE = ROOT / "references" / "examples" / "example-ui-compose-plan.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_input = load_module("composer_v211_validate_input", ROOT / "scripts" / "validate_input.py")
sys.modules["validate_input"] = validate_input
evidence_registry = load_module("composer_v211_registry", ROOT / "scripts" / "evidence_registry.py")
sys.modules["evidence_registry"] = evidence_registry
validate_plan = load_module("composer_v211_validate_plan", ROOT / "scripts" / "validate_plan.py")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ComposerV211EvidenceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.input = read_json(INPUT_EXAMPLE)
        cls.plan = read_json(PLAN_EXAMPLE)
        cls.registry = evidence_registry.build_evidence_registry(
            cls.input["layout_reference_analysis"],
            cls.input["style_profile"],
        )

    def errors(self, plan):
        return validate_plan.validate_document(plan, self.input)[0]

    def test_1_real_a_source_passes(self):
        self.assertTrue(self.registry.a_matches("top_status", "region"))
        self.assertEqual([], self.errors(self.plan))

    def test_2_fake_a_source_fails_at_exact_path(self):
        bad = copy.deepcopy(self.plan)
        bad["reference_application"]["layout"][0]["source_ids"][0] = "fake_layout_source_123"
        errors = self.errors(bad)
        self.assertTrue(any(
            item["code"] == "UNKNOWN_A_SOURCE_ID"
            and item["path"] == "$.reference_application.layout[0].source_ids[0]"
            and "fake_layout_source_123" in item["message"]
            and "region" in item["message"]
            for item in errors
        ))

    def test_3_real_b_trait_passes(self):
        record = self.registry.b_match("color_cool_blue_gray")
        self.assertIsNotNone(record)
        self.assertEqual(("color", "stable"), (record.dimension, record.classification))
        self.assertEqual([], self.errors(self.plan))

    def test_4_fake_b_trait_fails_at_exact_path(self):
        bad = copy.deepcopy(self.plan)
        bad["reference_application"]["style"][0]["trait_id"] = "fake_style_trait_123"
        errors = self.errors(bad)
        self.assertTrue(any(
            item["code"] == "UNKNOWN_B_TRAIT_ID"
            and item["path"] == "$.reference_application.style[0].trait_id"
            and "fake_style_trait_123" in item["message"]
            and "style_reference" in item["message"]
            for item in errors
        ))

    def test_5_user_requirement_origin_needs_no_a_source(self):
        decision = next(
            item for item in self.plan["reference_application"]["layout"]
            if item["origin"] == "user_requirement"
        )
        self.assertEqual([], decision["source_ids"])
        self.assertIsNone(decision["source_kind"])
        self.assertEqual([], self.errors(self.plan))

    def test_6_composer_derived_origin_needs_no_a_source(self):
        decision = next(
            item for item in self.plan["reference_application"]["layout"]
            if item["origin"] == "composer_derived"
        )
        self.assertEqual([], decision["source_ids"])
        self.assertIsNone(decision["source_kind"])
        self.assertEqual([], self.errors(self.plan))

    def test_7_layout_reference_requires_a_source(self):
        bad = copy.deepcopy(self.plan)
        bad["reference_application"]["layout"][0]["source_ids"] = []
        errors = self.errors(bad)
        self.assertTrue(any(
            item["code"] == "MISSING_A_SOURCE_IDS"
            and item["path"] == "$.reference_application.layout[0].source_ids"
            for item in errors
        ))

    def test_8_style_reference_requires_b_trait(self):
        bad = copy.deepcopy(self.plan)
        bad["reference_application"]["style"][0]["trait_id"] = None
        errors = self.errors(bad)
        self.assertTrue(any(
            item["code"] == "MISSING_B_TRAIT_ID"
            and item["path"] == "$.reference_application.style[0].trait_id"
            for item in errors
        ))

    def test_9_b_local_classification_cannot_be_changed(self):
        record = self.registry.b_match("color_local_red")
        self.assertIsNotNone(record)
        self.assertEqual("local", record.classification)
        bad = copy.deepcopy(self.plan)
        index = next(
            index
            for index, item in enumerate(bad["reference_application"]["style"])
            if item["trait_id"] == "color_local_red"
        )
        bad["reference_application"]["style"][index]["classification"] = "stable"
        errors = self.errors(bad)
        self.assertTrue(any(
            item["code"] == "B_TRAIT_CLASSIFICATION_MISMATCH"
            and item["path"] == f"$.reference_application.style[{index}].classification"
            for item in errors
        ))


if __name__ == "__main__":
    unittest.main()
