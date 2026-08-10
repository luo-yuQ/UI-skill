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


validate_input = load_module("composer_v2_validate_input", ROOT / "scripts" / "validate_input.py")
sys.modules["validate_input"] = validate_input
validate_plan = load_module("composer_v2_validate_plan", ROOT / "scripts" / "validate_plan.py")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ComposerV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.input = read_json(INPUT_EXAMPLE)
        cls.plan = read_json(PLAN_EXAMPLE)

    def input_errors(self, data):
        errors, _, _, _, _ = validate_input.validate_document(data)
        return errors

    def plan_errors(self, data):
        errors, _, _, _ = validate_plan.validate_document(data)
        return errors

    def component(self, component_id: str):
        return next(item for item in self.plan["component_tree"] if item["component_id"] == component_id)

    def test_1_valid_a_b_and_requirement_produce_valid_new_plan(self):
        self.assertEqual([], self.input_errors(self.input))
        self.assertEqual([], self.plan_errors(self.plan))
        self.assertEqual("2.0", self.input["schema_version"])
        self.assertEqual("2.0", self.plan["schema_version"])
        self.assertEqual("sky-vanguard-shop-final-001", self.plan["reference_application"]["layout_analysis_id"])
        self.assertEqual("dark_fantasy_reference_profile_01", self.plan["reference_application"]["style_profile_id"])
        self.assertEqual("guild_commission_board", self.plan["pages"][0]["page_id"])
        self.assertTrue(self.plan["reference_application"]["layout"])
        self.assertTrue(self.plan["reference_application"]["style"])

    def test_2_user_counts_override_a_visible_counts(self):
        a_groups = {item["group_id"]: item for item in self.input["layout_reference_analysis"]["component_groups"]}
        self.assertEqual(5, a_groups["category_tabs"]["visible_item_count"])
        self.assertEqual(6, a_groups["product_cards"]["visible_item_count"])
        self.assertEqual(3, self.component("category_tab_template")["repeat"]["count"])
        self.assertEqual(8, self.component("commission_card_template")["repeat"]["count"])
        decisions = {item["decision_id"]: item for item in self.plan["reference_application"]["layout"]}
        self.assertEqual("adapted", decisions["layout_category_count_adapted"]["disposition"])
        self.assertEqual("adapted", decisions["layout_grid_count_adapted"]["disposition"])

    def test_3_local_trait_is_not_globalized(self):
        local_traits = []
        for profile in self.input["style_profile"]["visual_profiles"].values():
            local_traits.extend(profile["local"])
        self.assertIn("color_local_red", {item["trait_id"] for item in local_traits})
        style_decisions = {item["trait_id"]: item for item in self.plan["reference_application"]["style"]}
        self.assertEqual("ignored", style_decisions["color_local_red"]["disposition"])
        directive_sources = {
            trait_id
            for directive in self.plan["visual_direction"]["directives"]
            for trait_id in directive["source_trait_ids"]
        }
        self.assertNotIn("color_local_red", directive_sources)

    def test_4_uncertain_trait_does_not_become_hard_fact(self):
        uncertain_traits = []
        for profile in self.input["style_profile"]["visual_profiles"].values():
            uncertain_traits.extend(profile["uncertain"])
        self.assertIn("world_magic_technology_balance", {item["trait_id"] for item in uncertain_traits})
        style_decisions = {item["trait_id"]: item for item in self.plan["reference_application"]["style"]}
        self.assertEqual("ignored", style_decisions["world_magic_technology_balance"]["disposition"])
        directive_sources = json.dumps(self.plan["visual_direction"]["directives"], ensure_ascii=False)
        self.assertNotIn("world_magic_technology_balance", directive_sources)
        hard_requirements = json.dumps(
            {
                "must_include": self.plan["generation_constraints"]["must_include"],
                "exact_counts": self.plan["generation_constraints"]["exact_counts"],
            },
            ensure_ascii=False,
        ).lower()
        self.assertNotIn("magical", hard_requirements)
        self.assertNotIn("technological", hard_requirements)

    def test_5_reference_semantics_do_not_leak_into_target_content(self):
        target_content = json.dumps(
            {
                "summary": self.plan["design_summary"],
                "pages": self.plan["pages"],
                "components": self.plan["component_tree"],
                "must_include": self.plan["generation_constraints"]["must_include"],
                "zones": self.plan["generation_constraints"]["key_content_zones"],
            },
            ensure_ascii=False,
        ).lower()
        for leaked_term in ("castle", "knight", "battle", "product", "price", "purchase"):
            self.assertNotIn(leaked_term, target_content)
        self.assertIn("commission", target_content)
        self.assertIn("guild", target_content)

    def test_6_legacy_pics_and_assets_are_rejected(self):
        with_pics = copy.deepcopy(self.input)
        with_pics["pics"] = []
        pics_errors = self.input_errors(with_pics)
        self.assertTrue(any(item["path"] == "$.pics" and item["code"] == "LEGACY_PICS_FORBIDDEN" for item in pics_errors))

        with_assets = copy.deepcopy(self.input)
        with_assets["assets"] = []
        asset_errors = self.input_errors(with_assets)
        self.assertTrue(any(item["path"] == "$.assets" and item["code"] == "LEGACY_ASSETS_FORBIDDEN" for item in asset_errors))

    def test_invalid_upstream_versions_stop_validation_at_paths(self):
        bad_a = copy.deepcopy(self.input)
        bad_a["layout_reference_analysis"]["schema_version"] = "9.9"
        self.assertTrue(any(item["path"] == "$.layout_reference_analysis.schema_version" for item in self.input_errors(bad_a)))

        bad_b = copy.deepcopy(self.input)
        bad_b["style_profile"]["schema_version"] = "9.9"
        self.assertTrue(any(item["path"] == "$.style_profile.schema_version" for item in self.input_errors(bad_b)))

    def test_v2_plan_has_no_v1_asset_fields(self):
        self.assertNotIn("asset_usages", self.plan)
        self.assertNotIn("missing_assets", self.plan)
        schema = read_json(ROOT / "schemas" / "ui-compose-plan.schema.json")
        self.assertNotIn("asset_usages", schema["properties"])
        self.assertNotIn("missing_assets", schema["properties"])


if __name__ == "__main__":
    unittest.main()
