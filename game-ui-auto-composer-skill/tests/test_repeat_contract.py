from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "references" / "examples" / "example-ui-compose-plan.json"
INPUT_PATH = ROOT / "references" / "examples" / "example-ui-compose-input.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


validate_input = load_module("repeat_contract_validate_input", ROOT / "scripts" / "validate_input.py")
sys.modules["validate_input"] = validate_input
evidence_registry = load_module("repeat_contract_evidence_registry", ROOT / "scripts" / "evidence_registry.py")
sys.modules["evidence_registry"] = evidence_registry
validate_plan = load_module("repeat_contract_validate_plan", ROOT / "scripts" / "validate_plan.py")


class RepeatContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_plan = read_json(PLAN_PATH)
        cls.input = read_json(INPUT_PATH)

    def plan_with_repeat(
        self,
        component_id: str,
        *,
        count: int,
        arrangement: str,
        columns,
        rows,
    ):
        plan = copy.deepcopy(self.base_plan)
        component = next(
            item for item in plan["component_tree"]
            if item["component_id"] == component_id
        )
        component["repeat"].update(
            count=count,
            arrangement=arrangement,
            columns=columns,
            rows=rows,
        )
        return plan

    def errors(self, plan):
        return validate_plan.validate_document(plan, self.input)[0]

    def test_1_legal_row_repeat_passes(self):
        plan = self.plan_with_repeat(
            "category_tab_template",
            count=3,
            arrangement="row",
            columns=None,
            rows=None,
        )
        self.assertEqual([], self.errors(plan))

    def test_2_row_with_grid_dimensions_fails(self):
        plan = self.plan_with_repeat(
            "category_tab_template",
            count=3,
            arrangement="row",
            columns=3,
            rows=1,
        )
        errors = self.errors(plan)
        component_index = next(
            index for index, item in enumerate(plan["component_tree"])
            if item["component_id"] == "category_tab_template"
        )
        self.assertTrue(any(
            item["code"] == "GRID_DIMENSIONS_UNEXPECTED"
            and item["path"] == f"$.component_tree[{component_index}].repeat"
            for item in errors
        ))

    def test_3_legal_column_repeat_passes(self):
        plan = self.plan_with_repeat(
            "category_tab_template",
            count=3,
            arrangement="column",
            columns=None,
            rows=None,
        )
        self.assertEqual([], self.errors(plan))

    def test_4_legal_grid_repeat_passes(self):
        plan = self.plan_with_repeat(
            "product_card_template",
            count=6,
            arrangement="grid",
            columns=2,
            rows=3,
        )
        self.assertEqual([], self.errors(plan))

    def test_5_guild_shop_categories_have_no_unexpected_grid_dimensions(self):
        errors = self.errors(copy.deepcopy(self.base_plan))
        self.assertNotIn(
            "GRID_DIMENSIONS_UNEXPECTED",
            {item["code"] for item in errors},
        )
        categories = next(
            item for item in self.base_plan["component_tree"]
            if item["component_id"] == "category_tab_template"
        )["repeat"]
        self.assertEqual(
            (3, "column", None, None),
            (
                categories["count"],
                categories["arrangement"],
                categories["columns"],
                categories["rows"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
