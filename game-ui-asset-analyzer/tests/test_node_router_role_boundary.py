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

    def test_routing_goal_is_useful_next_operation_not_category_purity(self):
        for required_text in (
            "not to assign the most philosophically precise UI category",
            "most useful next Stage2-A operation",
            "reduce visual-analysis complexity",
            "preserving independently meaningful, plausibly reusable visual assets",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)

    def test_router_uses_only_current_image_not_provenance_or_names(self):
        for forbidden_signal in (
            "`produced_by`",
            "parent role or parent name",
            "historical routes",
            "file names",
            "test-fixture names",
            "external UI taxonomy",
        ):
            with self.subTest(forbidden_signal=forbidden_signal):
                self.assertIn(forbidden_signal, self.prompt)
        self.assertIn(
            "Provenance shortcuts for `semantic_decompose` and `expand_instances` "
            "belong to engineering code",
            self.prompt,
        )

    def test_asset_does_not_require_absolute_visual_atomicity(self):
        for required_text in (
            "An asset does not need to be absolutely visually atomic",
            "independently meaningful and plausibly reusable visual assets, rather than visual fragments",
            "highlight, shadow, border, outline, internal texture, painted detail",
            "Those details alone are not a reason to continue recursion",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)

    def test_repeated_group_allows_instance_state_and_data_variation(self):
        for required_text in (
            "primary visual identity is a collection of multiple peer instances",
            "do not need to be pixel-identical",
            "Selected and unselected, open and closed, different reward contents",
            "Do not reject `repeated_group` merely because instance state or data differs",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)
        self.assertIn(
            "only one sibling inside a mixed module",
            self.prompt,
        )

    def test_structural_group_does_not_require_visible_container_boundary(self):
        for required_text in (
            "multiple semantically distinct major regions",
            "substantially reduce the scope and complexity",
            "small number of substantially simpler Direct Child regions",
            "Do not require a visible panel border, container frame, physical separator",
            "Visually adjacent but semantically independent major regions",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)

    def test_component_instance_is_default_for_useful_semantic_decomposition(self):
        for required_text in (
            "This is the natural default when the next useful operation is semantic decomposition",
            "Do not require every immediate child to already be a terminal asset",
            "Do not force `structural_group` merely because lightweight internal grouping",
            "without first creating substantially simpler major regions",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)

    def test_asset_component_uncertainty_fallback_prefers_recoverable_decomposition(self):
        for required_text in (
            "### Uncertainty fallback",
            "When uncertain between `asset` and `component_instance`, prefer `component_instance`",
            "Premature stopping can permanently lose those assets",
            "Do not continue decomposition merely because an object contains internal visual detail",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)

    def test_meaningful_boundary_guard_replaces_strict_flattening_guard(self):
        for required_text in (
            "### Meaningful-boundary guard",
            "visually and semantically meaningful enough",
            "reduce the next analysis scope",
            "Do not invent intermediate hierarchy solely because a conceptual UI structure could exist",
        ):
            with self.subTest(required_text=required_text):
                self.assertIn(required_text, self.prompt)
        self.assertNotIn("### Flattening Guard", self.prompt)

    def test_anti_over_splitting_guard_is_preserved(self):
        for required_text in (
            "### Anti-over-splitting Guard",
            "visual complexity, asset count, different asset responsibilities",
            "highlights, borders, textures, decoration",
            "Do not create a wrapper that only renames or regroups the same visual assets",
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

    def test_reason_explains_operation_without_taxonomy_essay(self):
        self.assertIn(
            "why the chosen next operation is useful",
            self.prompt,
        )
        self.assertIn(
            "rather than why the object philosophically belongs to a taxonomy",
            self.prompt,
        )

    def test_router_output_scope_and_generic_boundary_are_preserved(self):
        self.assertIn("Stage2-A Node Router v0.2 experiment", self.prompt)
        self.assertIn(
            "Return no Markdown, children, bboxes, `next_action`, taxonomy, assets, "
            "parent, analysis, tree, repeated instances, structural regions, or "
            "extraction strategy.",
            self.prompt,
        )
        self.assertIn("taxonomy, assets, parent, analysis, tree", self.prompt)
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
