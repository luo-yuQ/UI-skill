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
FIXTURE_DIR = ROOT / "tests" / "fixtures"
INPUT_EXAMPLE = FIXTURE_DIR / "example-ui-compose-input.json"
PLAN_EXAMPLE = FIXTURE_DIR / "example-ui-compose-plan.json"
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
finalize_hard_requirements = load_module(
    "composer_v211_regression_finalize_hard_requirements",
    ROOT / "scripts" / "finalize_hard_requirements.py",
)
sys.modules["finalize_hard_requirements"] = finalize_hard_requirements
validate_plan = load_module("composer_v211_regression_validate_plan", ROOT / "scripts" / "validate_plan.py")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ComposerV211RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.input = read_json(INPUT_EXAMPLE)
        cls.plan = finalize_hard_requirements.finalize_document(
            read_json(PLAN_EXAMPLE), cls.input["request"]["user_requirement"]
        )
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

    def test_primary_action_region_mapping_rule(self):
        skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`primary_mode_action_region`", skill_text)
        self.assertIn("`central_lower_action_band`", skill_text)
        self.assertIn("rather than inside the right auxiliary rail", skill_text)
        self.assertIn("bottom navigation region or bottom band may remain `ignored`", skill_text)

    def test_runtime_skill_does_not_reference_complete_plan_fixture(self):
        skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("example-ui-compose-plan.json", skill_text)
        self.assertNotIn("references/examples", skill_text)
        self.assertIn("schemas/ui-compose-plan.schema.json", skill_text)
        self.assertIn("references/output-schema.md", skill_text)

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
        self.assertEqual("central_lower_action_band", self.component("refresh_button")["parent_id"])
        refresh_layout = next(item for item in self.plan["layout_rules"] if item["component_id"] == "refresh_button")
        self.assertEqual("center", refresh_layout["anchor"])
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

    def test_llm_hard_requirement_proposal_is_rejected_until_finalized(self):
        bad = copy.deepcopy(self.plan)
        bad["project_context"]["hard_requirements"]["required_elements"].append(
            {
                "fact_id": "reference_promoted_detail",
                "target_component_id": "auxiliary_shop_rail",
                "semantic": "detail_panel",
                "position": None,
                "evidence": "参考提供的布局结构",
            }
        )
        self.assertIn(
            "HARD_REQUIREMENTS_NOT_FINALIZED",
            {item["code"] for item in self.plan_errors(bad)},
        )

    def test_recharge_final_plan_keeps_a_derived_10_item_5x2_outside_hard_requirements(self):
        requirement = "参考这个充值界面的布局，帮我设计一个新的游戏充值页面。"
        recharge_input = copy.deepcopy(self.input)
        recharge_input["request"]["user_requirement"] = requirement
        candidate = copy.deepcopy(self.plan)
        candidate["project_context"]["user_requirement"] = requirement
        candidate["pages"][0]["page_type"] = "recharge_page"

        product = next(
            item for item in candidate["component_tree"]
            if item["component_id"] == "product_card_template"
        )
        product["repeat"].update(count=10, arrangement="grid", columns=5, rows=2)
        exact = next(
            item for item in candidate["generation_constraints"]["exact_counts"]
            if item["component_id"] == "product_card_template"
        )
        exact["count"] = 10
        grid = next(
            item for item in candidate["generation_constraints"]["grid_specs"]
            if item["component_id"] == "product_card_template"
        )
        grid.update(columns=5, rows=2)
        reference_before = copy.deepcopy(candidate["reference_application"])
        constraints_before = copy.deepcopy(candidate["generation_constraints"])

        finalized = finalize_hard_requirements.finalize_document(
            candidate, requirement
        )
        hard = finalized["project_context"]["hard_requirements"]
        self.assertEqual("recharge_page", hard["page_semantic"]["value"])
        self.assertEqual([], hard["explicit_counts"])
        self.assertEqual([], hard["grid_requirements"])
        self.assertEqual([], hard["required_elements"])
        self.assertEqual(reference_before, finalized["reference_application"])
        self.assertEqual(constraints_before, finalized["generation_constraints"])
        self.assertEqual([], validate_plan.validate_document(finalized, recharge_input)[0])

    def test_a_driven_layout_uses_major_skeleton_when_positions_are_not_locked(self):
        hard = self.plan["project_context"]["hard_requirements"]
        self.assertTrue(all(item["position"] is None for item in hard["required_elements"]))

        major_region_ids = {
            "page_background",
            "top_status",
            "category_navigation",
            "product_list",
            "product_detail",
            "purchase_action",
        }
        region_decisions = {
            source_id: decision
            for decision in self.plan["reference_application"]["layout"]
            if decision["origin"] == "layout_reference" and decision["source_kind"] == "region"
            for source_id in decision["source_ids"]
        }
        self.assertTrue(major_region_ids.issubset(region_decisions))
        for source_id in major_region_ids:
            self.assertIn(region_decisions[source_id]["disposition"], {"adopted", "adapted"})

        components = {item["component_id"]: item for item in self.plan["component_tree"]}
        self.assertEqual("content_area", components["category_navigation"]["parent_id"])
        self.assertEqual("content_area", components["product_grid"]["parent_id"])
        self.assertEqual("content_area", components["central_lower_action_band"]["parent_id"])
        self.assertEqual("content_area", components["auxiliary_shop_rail"]["parent_id"])
        self.assertEqual("central_lower_action_band", components["refresh_button"]["parent_id"])
        self.assertIn("dominant central", components["product_grid"]["design_intent"].lower())
        self.assertIn("narrow", components["auxiliary_shop_rail"]["design_intent"].lower())
        self.assertIn("secondary", components["auxiliary_shop_rail"]["design_intent"].lower())

        layouts = {item["component_id"]: item for item in self.plan["layout_rules"]}
        self.assertEqual("center_left", layouts["category_navigation"]["anchor"])
        self.assertEqual("center", layouts["product_grid"]["anchor"])
        self.assertEqual("bottom_center", layouts["central_lower_action_band"]["anchor"])
        self.assertEqual("center_right", layouts["auxiliary_shop_rail"]["anchor"])
        self.assertEqual("top_center", layouts["top_currency_bar"]["anchor"])
        self.assertEqual("parent", layouts["refresh_button"]["relative_to"])
        self.assertEqual("center", layouts["refresh_button"]["anchor"])
        self.assertGreater(layouts["product_grid"]["dimensions"]["width"], layouts["category_navigation"]["dimensions"]["width"])
        self.assertGreater(layouts["product_grid"]["dimensions"]["width"], 2 * layouts["auxiliary_shop_rail"]["dimensions"]["width"])
        self.assertEqual(layouts["product_grid"]["dimensions"]["width"], layouts["central_lower_action_band"]["dimensions"]["width"])
        action_relationships = layouts["central_lower_action_band"]["relationships"]
        self.assertTrue(any(item["relationship_type"] == "below" and item["target_component_id"] == "product_grid" for item in action_relationships))
        containment = next(item for item in self.plan["reference_application"]["layout"] if item["decision_id"] == "a_detail_action_containment")
        self.assertEqual("ignored", containment["disposition"])
        self.assertIsNone(containment["target_application"])

        categories = components["category_tab_template"]["repeat"]
        products = components["product_card_template"]["repeat"]
        self.assertEqual((3, "column", None, None), (categories["count"], categories["arrangement"], categories["columns"], categories["rows"]))
        self.assertEqual((6, "grid", 2, 3), (products["count"], products["arrangement"], products["columns"], products["rows"]))
        self.assertEqual([], self.plan_errors(self.plan))

    def test_explicit_locked_positions_override_a_layout(self):
        locked_input = copy.deepcopy(self.input)
        locked_plan = copy.deepcopy(self.plan)
        requirement = (
            "做一个公会商店。必须在左侧放 3 个分类，商品必须在右侧保持 2 x 3，共 6 个商品。"
            "显示金币和公会币。一个刷新按钮必须固定在底部。"
        )
        locked_input["request"]["user_requirement"] = requirement
        locked_plan["project_context"]["user_requirement"] = requirement
        locked_plan = finalize_hard_requirements.finalize_document(
            locked_plan, requirement
        )
        hard = locked_plan["project_context"]["hard_requirements"]
        requirements = {item["target_component_id"]: item for item in hard["required_elements"]}

        layouts = {item["component_id"]: item for item in locked_plan["layout_rules"]}
        layouts["category_navigation"]["anchor"] = "center_left"
        layouts["product_grid"]["anchor"] = "center_right"
        layouts["refresh_button"]["anchor"] = "bottom_center"
        product_region = next(
            item for item in locked_plan["reference_application"]["layout"]
            if item["decision_id"] == "a_product_region"
        )
        product_region.update(
            disposition="ignored",
            target_application=None,
            rationale="The user's explicit locked-right product position conflicts with A's central product region.",
        )

        errors = validate_plan.validate_document(locked_plan, locked_input)[0]
        self.assertEqual([], errors)
        self.assertEqual("right", requirements["product_grid"]["position"])
        self.assertIn("right", layouts["product_grid"]["anchor"])
        self.assertEqual("ignored", product_region["disposition"])
        self.assertIn("explicit locked-right", product_region["rationale"])


if __name__ == "__main__":
    unittest.main()
