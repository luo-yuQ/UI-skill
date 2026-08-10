from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
INPUT_EXAMPLE = ROOT / "references" / "examples" / "example-ui-compose-input.json"
PLAN_EXAMPLE = ROOT / "references" / "examples" / "example-ui-compose-plan.json"
A_SOURCE = WORKSPACE / "game-ui-layout-analysis-verifier" / "examples" / "example-final-analysis.json"
B_SOURCE = WORKSPACE / "game-ui-style-reference-analyzer" / "examples" / "b2-style-profile.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_input = load_module("composer_v211_regression_validate_input", ROOT / "scripts" / "validate_input.py")
sys.modules["validate_input"] = validate_input
evidence_registry = load_module("composer_v211_evidence_registry", ROOT / "scripts" / "evidence_registry.py")
sys.modules["evidence_registry"] = evidence_registry
validate_plan = load_module("composer_v211_regression_validate_plan", ROOT / "scripts" / "validate_plan.py")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ComposerV211RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.input = read_json(INPUT_EXAMPLE)
        cls.plan = read_json(PLAN_EXAMPLE)
        cls.original_a = read_json(A_SOURCE)
        cls.original_b = read_json(B_SOURCE)

    def input_errors(self, data, integrity=False):
        sources = (self.original_a, self.original_b) if integrity else (None, None)
        return validate_input.validate_document(data, *sources)[0]

    def plan_errors(self, data):
        return validate_plan.validate_document(data, self.input)[0]

    def component(self, component_id):
        return next(item for item in self.plan["component_tree"] if item["component_id"] == component_id)

    def test_fixture_is_valid_v211(self):
        self.assertEqual([], self.input_errors(self.input, True))
        self.assertEqual([], self.plan_errors(self.plan))
        self.assertEqual("2.1.1", self.input["schema_version"])
        self.assertEqual("2.1.1", self.plan["schema_version"])

    def test_1_user_product_count_and_grid(self):
        product = self.component("product_card_template")["repeat"]
        self.assertEqual((6, 2, 3), (product["count"], product["columns"], product["rows"]))
        output = json.dumps(self.plan, ensure_ascii=False).lower()
        self.assertNotIn("8 products", output)
        self.assertNotIn("eight product cards", output)
        self.assertNotRegex(output, r'"count"\s*:\s*8')

    def test_2_business_semantic_does_not_drift(self):
        output = json.dumps(self.plan, ensure_ascii=False).lower()
        for term in ("commission", "quest", "mission", "accept commission", "commission detail"):
            self.assertIsNone(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", output))
        self.assertEqual("guild_shop", self.plan["pages"][0]["page_type"])
        self.assertEqual("guild_shop", self.plan["project_context"]["hard_requirements"]["page_semantic"]["value"])

    def test_3_a_traceability(self):
        allowed = validate_plan.collect_a_ids(self.original_a)
        for decision in self.plan["reference_application"]["layout"]:
            for source_id in decision["source_ids"]:
                self.assertIn(source_id, allowed[decision["source_kind"]])
        bad = copy.deepcopy(self.plan)
        bad["reference_application"]["layout"][0]["source_ids"] = ["missing_a_id"]
        self.assertIn("UNKNOWN_A_SOURCE_ID", {item["code"] for item in self.plan_errors(bad)})

    def test_4_b_traceability(self):
        traits = validate_plan.collect_b_traits(self.original_b)
        for decision in self.plan["reference_application"]["style"]:
            if decision["origin"] != "style_reference":
                continue
            self.assertIn(decision["trait_id"], traits)
            self.assertEqual(traits[decision["trait_id"]], (decision["dimension"], decision["classification"]))
        bad = copy.deepcopy(self.plan)
        bad["reference_application"]["style"][0]["trait_id"] = "missing_b_trait"
        self.assertIn("UNKNOWN_B_TRAIT_ID", {item["code"] for item in self.plan_errors(bad)})

    def test_5_a_is_immutable(self):
        self.assertEqual(self.original_a, self.input["layout_reference_analysis"])
        bad = copy.deepcopy(self.input)
        bad["layout_reference_analysis"]["overall_confidence"] = 0.95
        errors = self.input_errors(bad, True)
        self.assertTrue(any(item["code"] == "UPSTREAM_INTEGRITY_MISMATCH" and item["path"] == "$.layout_reference_analysis.overall_confidence" for item in errors))

    def test_6_b_is_immutable(self):
        self.assertEqual(self.original_b, self.input["style_profile"])
        bad = copy.deepcopy(self.input)
        bad["style_profile"]["overall_confidence"] = 0.95
        errors = self.input_errors(bad, True)
        self.assertTrue(any(item["code"] == "UPSTREAM_INTEGRITY_MISMATCH" and item["path"] == "$.style_profile.overall_confidence" for item in errors))

    def test_7_local_scope(self):
        traits = validate_plan.collect_b_traits(self.original_b)
        decisions = {item["trait_id"]: item for item in self.plan["reference_application"]["style"]}
        for trait_id, (_, classification) in traits.items():
            if classification == "local" and trait_id in decisions:
                decision = decisions[trait_id]
                if decision["disposition"] in ("adopted", "conditionally_adopted", "overridden_by_user"):
                    self.assertTrue(decision["promoted_by_user_requirement"] or len(decision["target_scope"]) == 1)
                else:
                    self.assertEqual("ignored", decision["disposition"])
        bad = copy.deepcopy(self.plan)
        local = next(item for item in bad["reference_application"]["style"] if item["classification"] == "local")
        local["disposition"] = "adopted"
        local["target_scope"] = ["guild_shop_root", "product_grid"]
        local["target_application"] = "Global panel language."
        self.assertIn("LOCAL_TRAIT_SCOPE_VIOLATION", {item["code"] for item in self.plan_errors(bad)})

    def test_8_cross_section_consistency(self):
        hard = self.plan["project_context"]["hard_requirements"]
        counts = {item["target_component_id"]: item["count"] for item in hard["explicit_counts"]}
        generated = {item["component_id"]: item["count"] for item in self.plan["generation_constraints"]["exact_counts"]}
        for component_id, count in counts.items():
            actual = self.component(component_id).get("repeat", {}).get("count", 1)
            self.assertEqual(count, actual)
            self.assertEqual(count, generated[component_id])
        grid = hard["grid_requirements"][0]
        grid_out = self.plan["generation_constraints"]["grid_specs"][0]
        self.assertEqual((2, 3), (grid["columns"], grid["rows"]))
        self.assertEqual((2, 3), (grid_out["columns"], grid_out["rows"]))
        refresh = next(item for item in self.plan["interactions"] if item["trigger_component_id"] == "refresh_button")
        self.assertIn("refresh", refresh["action"])
        refresh_layout = next(item for item in self.plan["layout_rules"] if item["component_id"] == "refresh_button")
        self.assertIn("bottom", refresh_layout["anchor"])
        self.assertEqual([], self.plan_errors(self.plan))

    def test_legacy_fields_are_rejected(self):
        for field, code in (("pics", "LEGACY_PICS_FORBIDDEN"), ("assets", "LEGACY_ASSETS_FORBIDDEN")):
            bad = copy.deepcopy(self.input)
            bad[field] = []
            self.assertIn(code, {item["code"] for item in self.input_errors(bad)})

    def test_requirement_evidence_is_exact_substring(self):
        bad = copy.deepcopy(self.plan)
        bad["project_context"]["hard_requirements"]["explicit_counts"][0]["evidence"] = "invented evidence"
        self.assertIn("REQUIREMENT_EVIDENCE_MISMATCH", {item["code"] for item in self.plan_errors(bad)})


if __name__ == "__main__":
    unittest.main()
