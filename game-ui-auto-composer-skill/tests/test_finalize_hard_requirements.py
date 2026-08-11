from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "finalize_hard_requirements.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


finalizer = load_module("focused_finalize_hard_requirements", SCRIPT_PATH)


class FinalizeHardRequirementsTests(unittest.TestCase):
    def test_recharge_case_ignores_llm_and_preserves_a_derived_sections(self):
        requirement = "参考这个充值界面的布局，帮我设计一个新的游戏充值页面。"
        candidate = {
            "project_context": {
                "user_requirement": requirement,
                "hard_requirements": {
                    "page_semantic": {
                        "fact_id": "fake",
                        "value": "detail_panel",
                        "evidence": "参考这个充值界面的布局",
                    },
                    "explicit_counts": [
                        {
                            "fact_id": "from_a",
                            "target_component_id": "purchase_card",
                            "count": 10,
                            "evidence": "参考这个充值界面的布局",
                        }
                    ],
                    "grid_requirements": [],
                    "required_elements": [
                        {
                            "fact_id": "from_a_detail",
                            "target_component_id": "detail_panel",
                            "semantic": "detail_panel",
                            "position": None,
                            "evidence": "参考这个充值界面的布局",
                        }
                    ],
                    "must_include": [],
                    "must_not_include": [],
                },
            },
            "reference_application": {
                "layout": [
                    {
                        "source_ids": ["purchase_grid", "detail_panel"],
                        "disposition": "adopted",
                    }
                ]
            },
            "generation_constraints": {
                "exact_counts": [{"component_id": "purchase_card", "count": 10}],
                "grid_specs": [
                    {"component_id": "purchase_card", "columns": 5, "rows": 2}
                ],
            },
        }
        reference_before = copy.deepcopy(candidate["reference_application"])
        constraints_before = copy.deepcopy(candidate["generation_constraints"])

        result = finalizer.finalize_document(candidate, requirement)
        hard = result["project_context"]["hard_requirements"]
        self.assertEqual("recharge_page", hard["page_semantic"]["value"])
        self.assertEqual("新的游戏充值页面", hard["page_semantic"]["evidence"])
        self.assertEqual([], hard["explicit_counts"])
        self.assertEqual([], hard["grid_requirements"])
        self.assertEqual([], hard["required_elements"])
        self.assertEqual([], hard["must_include"])
        self.assertEqual([], hard["must_not_include"])
        self.assertEqual(reference_before, result["reference_application"])
        self.assertEqual(constraints_before, result["generation_constraints"])

    def test_explicit_arabic_counts_grid_and_elements(self):
        requirement = (
            "做一个商城页面，需要4个奖励、6个商品和3个按钮。"
            "商品按5列2行排列，必须有购买按钮，包含角色头像，要有倒计时。"
        )
        hard = finalizer.derive_hard_requirements(requirement)
        counts = {item["target_component_id"]: item["count"] for item in hard["explicit_counts"]}
        self.assertEqual(4, counts["reward_item_template"])
        self.assertEqual(6, counts["product_card_template"])
        self.assertEqual(3, counts["button_template"])
        grid = hard["grid_requirements"][0]
        self.assertEqual((5, 2), (grid["columns"], grid["rows"]))
        elements = {item["semantic"] for item in hard["required_elements"]}
        self.assertTrue(
            {"purchase_button", "character_avatar", "countdown"}.issubset(elements)
        )

    def test_supported_grid_forms(self):
        cases = (
            ("需要10个商品，商品按5x2排列。", (5, 2)),
            ("需要10个商品，商品做成两行五列。", (5, 2)),
            ("需要10个商品，商品每行5个。", (5, 2)),
        )
        for requirement, expected in cases:
            with self.subTest(requirement=requirement):
                hard = finalizer.derive_hard_requirements(requirement)
                grid = hard["grid_requirements"][0]
                self.assertEqual(expected, (grid["columns"], grid["rows"]))
                self.assertTrue(grid["evidence"])
                self.assertIn(grid["evidence"], requirement)

    def test_only_explicit_include_and_exclude_clauses_are_kept(self):
        requirement = "做一个商店页面，必须包含每日折扣，不要出现自动购买。参考这个布局。"
        hard = finalizer.derive_hard_requirements(requirement)
        self.assertEqual(["每日折扣"], hard["must_include"])
        self.assertEqual(["自动购买"], hard["must_not_include"])
        self.assertNotIn("参考这个布局", hard["must_include"])

    def test_unsupported_ambiguous_language_stays_empty(self):
        hard = finalizer.derive_hard_requirements(
            "参考这个布局，做一个更丰富的新界面，放十二个商品。"
        )
        self.assertIsNone(hard["page_semantic"])
        self.assertEqual([], hard["explicit_counts"])
        self.assertEqual([], hard["grid_requirements"])
        self.assertEqual([], hard["required_elements"])

    def test_cli_overwrites_candidate_in_place(self):
        requirement = "参考这个充值界面的布局，帮我设计一个新的游戏充值页面。"
        candidate = {
            "project_context": {
                "user_requirement": requirement,
                "hard_requirements": {"untrusted": True},
            }
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan_path = root / "ui-compose-plan.json"
            request_path = root / "request.json"
            plan_path.write_text(
                json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
            )
            request_path.write_text(
                json.dumps({"user_requirement": requirement}, ensure_ascii=False),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    str(plan_path),
                    "--request",
                    str(request_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            written = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "recharge_page",
                written["project_context"]["hard_requirements"]["page_semantic"]["value"],
            )


if __name__ == "__main__":
    unittest.main()
