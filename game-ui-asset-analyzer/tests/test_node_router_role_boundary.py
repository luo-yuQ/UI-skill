from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REFERENCE_PATH = ROOT / "references" / "node-router-v0.1.md"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from production_visual_adapter import ProductionVisualAdapter  # noqa: E402


class NodeRouterRoleBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference = REFERENCE_PATH.read_text(encoding="utf-8")
        cls.prompt = ProductionVisualAdapter._load_prompt(REFERENCE_PATH)

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
        self.assertIn("Stage2-A Node Router v0.1.1", self.prompt)
        self.assertIn(
            "Return no Markdown, children, bboxes, `next_action`, taxonomy, tree, "
            "repeated instances, structural regions, or extraction strategy.",
            self.prompt,
        )
        self.assertNotIn("region_004", self.prompt)
        self.assertNotIn("item detail panel", self.prompt.lower())
        self.assertIn(
            "A self-contained component was occasionally classified as "
            "`structural_group` because its internal assets had different "
            "responsibilities.",
            self.reference,
        )


if __name__ == "__main__":
    unittest.main()
