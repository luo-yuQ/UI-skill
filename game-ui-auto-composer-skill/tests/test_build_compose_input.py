from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
A_SOURCE = WORKSPACE / "game-ui-layout-analysis-verifier" / "examples" / "example-final-analysis.json"
B_SOURCE = WORKSPACE / "game-ui-style-reference-analyzer" / "examples" / "b2-style-profile.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_module("composer_build_compose_input", ROOT / "scripts" / "build_compose_input.py")
validate_input = load_module("composer_build_input_validate_input", ROOT / "scripts" / "validate_input.py")


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class BuildComposeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_a = read_json(A_SOURCE)
        cls.source_b = read_json(B_SOURCE)

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def build(self, user_requirement: str, **request_metadata) -> tuple[dict, Path]:
        request_path = self.temp / "request.json"
        output_path = self.temp / "ui-compose-input.json"
        write_json(
            request_path,
            {"user_requirement": user_requirement, **request_metadata},
        )
        document = builder.build_compose_input(
            request_path=request_path,
            layout_path=A_SOURCE,
            style_path=B_SOURCE,
            output_path=output_path,
        )
        return document, output_path

    def test_1_chinese_user_requirement_is_exact(self):
        requirement = "我想做一个暗黑幻想风格的公会商店页面。"
        document, _ = self.build(
            requirement,
            layout_input_path="10-layout-reference/layout-analysis.json",
            style_input_path="20-style-reference/style-profile.json",
        )
        self.assertEqual(requirement, document["request"]["user_requirement"])
        self.assertEqual({"user_requirement"}, set(document["request"]))

    def test_2_mixed_utf8_text_has_no_replacement_question_marks(self):
        requirement = "右侧显示 6 个商品，按 2 列 × 3 行排列。 English UI 2026！"
        document, output_path = self.build(requirement)
        self.assertEqual(requirement, document["request"]["user_requirement"])
        self.assertNotIn("?", document["request"]["user_requirement"])
        self.assertIn(requirement, output_path.read_bytes().decode("utf-8"))

    def test_3_embedded_layout_is_deep_equal_to_source_a(self):
        document, _ = self.build("构建布局输入。")
        self.assertTrue(
            builder.json_value_equal(
                document["layout_reference_analysis"],
                self.source_a,
            )
        )

    def test_4_embedded_style_is_deep_equal_to_source_b(self):
        document, _ = self.build("构建风格输入。")
        self.assertTrue(
            builder.json_value_equal(
                document["style_profile"],
                self.source_b,
            )
        )

    def test_5_built_document_passes_current_input_validator(self):
        document, _ = self.build("右侧显示 6 个商品，按 2 列 × 3 行排列。")
        errors, _, _, _, _ = validate_input.validate_document(
            document,
            self.source_a,
            self.source_b,
        )
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
