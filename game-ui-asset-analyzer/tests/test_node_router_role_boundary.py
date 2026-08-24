from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REFERENCE_PATH = ROOT / "references" / "node-router-v0.1.md"
HIERARCHY_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "router-hierarchy-role-boundaries.json"
)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from production_visual_adapter import ProductionVisualAdapter  # noqa: E402


class NodeRouterRoleBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference = REFERENCE_PATH.read_text(encoding="utf-8")
        cls.prompt = ProductionVisualAdapter._load_prompt(REFERENCE_PATH)
        cls.hierarchy_fixture = json.loads(
            HIERARCHY_FIXTURE_PATH.read_text(encoding="utf-8")
        )

    def test_component_instance_boundary_allows_internally_diverse_assets(self):
        self.assertIn(
            "multiple internally distinct visual elements with different "
            "responsibilities and still be a single `component_instance`",
            self.prompt,
        )
        self.assertIn(
            "one self-contained UI object",
            self.prompt,
        )
        self.assertIn(
            "artwork, icon, text, status, or controls",
            self.prompt,
        )
        self.assertIn(
            "Do not classify a node as `structural_group` merely because its "
            "internal visual assets serve different responsibilities.",
            self.prompt,
        )

    def test_structural_group_boundary_requires_structural_direct_children(self):
        self.assertIn(
            "meaningful structural regions, sections, containers, or collections",
            self.prompt,
        )
        self.assertIn(
            "another `structural_split` would materially reduce visual complexity",
            self.prompt,
        )

    def test_natural_next_step_check_distinguishes_the_two_roles(self):
        self.assertIn(
            "what kind of Direct Children would naturally be produced",
            self.prompt,
        )
        self.assertIn(
            "icon, illustration, text, button, status, or decoration",
            self.prompt,
        )
        self.assertIn(
            "header region, content region, sidebar, section, collection, panel "
            "group, or functional area",
            self.prompt,
        )

    def test_flattening_guard_blocks_skipping_owned_intermediate_nodes(self):
        for required_text in (
            "### Flattening Guard",
            "Being self-contained is not sufficient",
            "immediate owned Direct Children",
            "must not skip an independently meaningful intermediate ownership boundary",
            "container, collection, repeated collection, slot, card, row, cell, "
            "item instance, or subcomponent",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)

        self.assertIn(
            "If multiple peer instances each own their own visual assets",
            self.prompt,
        )
        self.assertIn(
            "preserve the collection as one Direct Child",
            self.prompt,
        )

    def test_anti_over_splitting_guard_rejects_synthetic_ownership_wrappers(self):
        for required_text in (
            "### Anti-over-splitting Guard",
            "Do not invent an intermediate node merely to satisfy the ownership concept",
            "stable, independently meaningful component-tree unit",
            "Do not create a wrapper that only renames or regroups the same direct visual assets",
            "Visual complexity, asset count, or different asset responsibilities alone",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)

    def test_three_way_role_boundary_regression_fixture(self):
        self.assertEqual(
            "node-router-v0.1.2", self.hierarchy_fixture["contract_version"]
        )
        actual = {
            case["id"]: case["expected_node_role"]
            for case in self.hierarchy_fixture["cases"]
        }
        self.assertEqual(
            {
                "item_slot": "component_instance",
                "wheel_with_slot_collection": "structural_group",
                "slot_collection": "repeated_group",
            },
            actual,
        )

        cases = {case["id"]: case for case in self.hierarchy_fixture["cases"]}
        self.assertIn("panel and item artwork", cases["item_slot"]["semantic_basis"])
        self.assertIn(
            "repeated slot collection",
            cases["wheel_with_slot_collection"]["semantic_basis"],
        )
        self.assertIn(
            "peer slot instances", cases["slot_collection"]["semantic_basis"]
        )

    def test_asset_and_repeated_group_definitions_remain_unchanged(self):
        self.assertIn(
            "`asset`: the Current Node is already one coherent visual asset and "
            "further recursion has no clear engineering value.",
            self.prompt,
        )
        self.assertIn(
            "`repeated_group`: the Current Node's primary identity is a collection "
            "of enumerable peer instances with the same component/business semantics",
            self.prompt,
        )

    def test_router_output_scope_and_generic_boundary_are_preserved(self):
        self.assertIn("Stage2-A Node Router v0.1.2", self.prompt)
        self.assertIn(
            "Return no Markdown, children, bboxes, `next_action`, taxonomy, tree, "
            "repeated instances, structural regions, or extraction strategy.",
            self.prompt,
        )
        self.assertNotIn("region_004", self.prompt)
        self.assertNotIn("item detail panel", self.prompt.lower())
        self.assertNotIn("prize wheel", self.prompt.lower())
        self.assertNotIn("potion", self.prompt.lower())
        self.assertIn(
            "A self-contained component was occasionally classified as "
            "`structural_group` because its internal assets had different "
            "responsibilities.",
            self.reference,
        )
        self.assertIn(
            "A self-contained module was classified as `component_instance` even "
            "though semantic decomposition would skip meaningful intermediate "
            "ownership boundaries.",
            self.reference,
        )


if __name__ == "__main__":
    unittest.main()
