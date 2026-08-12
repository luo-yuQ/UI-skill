from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_asset_analysis as builder  # noqa: E402
import validate_asset_analysis as validator  # noqa: E402


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class AssetAnalysisTests(unittest.TestCase):
    def assert_has_error(self, errors: list[str], text: str) -> None:
        self.assertTrue(
            any(text.casefold() in error.casefold() for error in errors),
            f"Expected an error containing {text!r}, got: {errors}",
        )

    def test_schemas_are_valid_draft_2020_12(self):
        for schema_path in (
            validator.CANDIDATE_SCHEMA_PATH,
            validator.ANALYSIS_SCHEMA_PATH,
        ):
            with self.subTest(schema=schema_path.name):
                schema = validator.load_schema(schema_path)
                self.assertEqual(
                    "https://json-schema.org/draft/2020-12/schema",
                    schema["$schema"],
                )
                Draft202012Validator.check_schema(schema)

    def test_cases_1_to_3_are_valid(self):
        candidates = load_fixture("valid-candidates.json")
        self.assertEqual([], validator.validate_candidates(candidates, (100, 80)))
        self.assertEqual("text", candidates[0]["semantic_type"])
        self.assertEqual("do_not_extract", candidates[0]["strategy"])
        self.assertEqual("direct_crop", candidates[1]["strategy"])
        self.assertEqual("advanced_required", candidates[2]["strategy"])
        self.assertEqual(["text_baked_in"], candidates[2]["issues"])

    def test_case_4_out_of_bounds_bbox_fails(self):
        candidates = load_fixture("invalid-bbox-out-of-bounds.json")
        errors = validator.validate_candidates(candidates, (100, 80))
        self.assert_has_error(errors, "exceeds source width")

    def test_case_5_false_with_direct_crop_fails(self):
        candidates = load_fixture("invalid-strategy-mismatch.json")
        errors = validator.validate_candidates(candidates, (100, 80))
        self.assert_has_error(errors, "should_extract")

    def test_case_6_advanced_without_issues_fails(self):
        candidates = load_fixture("invalid-advanced-no-issues.json")
        errors = validator.validate_candidates(candidates, (100, 80))
        self.assert_has_error(errors, "issues")

    def test_builder_reads_source_size_sorts_and_assigns_ids(self):
        candidates = load_fixture("valid-candidates.json")
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            image_path = temp / "preview.png"
            Image.new("RGBA", (100, 80), (0, 0, 0, 0)).save(image_path)

            analysis = builder.build_analysis(image_path, candidates)

        self.assertEqual("0.1", analysis["schema_version"])
        self.assertEqual("preview.png", analysis["source_image"])
        self.assertEqual({"width": 100, "height": 80}, analysis["source_size"])
        self.assertEqual(
            "game-ui-asset-taxonomy-v0.1",
            analysis["taxonomy_version"],
        )
        self.assertEqual(
            ["icon_001", "text_001", "button_001"],
            [asset["id"] for asset in analysis["assets"]],
        )

    def test_ids_are_numbered_per_semantic_type(self):
        candidates = load_fixture("valid-candidates.json")
        second_icon = copy.deepcopy(candidates[1])
        second_icon["label"] = "Second icon"
        second_icon["bbox"] = {"x": 30, "y": 5, "width": 16, "height": 16}
        candidates.append(second_icon)

        with tempfile.TemporaryDirectory() as raw:
            image_path = Path(raw) / "preview.png"
            Image.new("RGB", (100, 80), "white").save(image_path)
            analysis = builder.build_analysis(image_path, candidates)

        self.assertEqual(
            ["icon_001", "icon_002"],
            [
                asset["id"]
                for asset in analysis["assets"]
                if asset["semantic_type"] == "icon"
            ],
        )

    def test_exact_bbox_ties_preserve_input_order(self):
        candidates = load_fixture("valid-candidates.json")
        tied = copy.deepcopy(candidates[1])
        tied["label"] = "Tied icon"
        candidates.insert(1, tied)

        with tempfile.TemporaryDirectory() as raw:
            image_path = Path(raw) / "preview.png"
            Image.new("RGB", (100, 80), "white").save(image_path)
            analysis = builder.build_analysis(image_path, candidates)

        self.assertEqual(
            ["Tied icon", "Currency icon"],
            [asset["label"] for asset in analysis["assets"][:2]],
        )

    def test_missing_source_image_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "missing.png"
            with self.assertRaisesRegex(ValueError, "does not exist"):
                builder.build_analysis(missing, load_fixture("valid-candidates.json"))

    def test_builder_and_validator_cli_succeed(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            image_path = temp / "preview.png"
            output_path = temp / "asset-analysis.json"
            Image.new("RGB", (100, 80), "white").save(image_path)

            build_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_asset_analysis.py"),
                    "--source-image",
                    str(image_path),
                    "--model-output",
                    str(FIXTURES / "valid-candidates.json"),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, build_result.returncode, build_result.stderr)

            validate_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_asset_analysis.py"),
                    str(output_path),
                    "--source-image",
                    str(image_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, validate_result.returncode, validate_result.stderr)
        self.assertIn("Valid asset analysis", validate_result.stdout)

    def test_invalid_cli_does_not_write_output(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            image_path = temp / "preview.png"
            output_path = temp / "asset-analysis.json"
            Image.new("RGB", (100, 80), "white").save(image_path)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_asset_analysis.py"),
                    "--source-image",
                    str(image_path),
                    "--model-output",
                    str(FIXTURES / "invalid-strategy-mismatch.json"),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(output_path.exists())
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
