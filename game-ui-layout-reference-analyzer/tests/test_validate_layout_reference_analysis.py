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
SCRIPT_PATH = ROOT / "scripts" / "validate_layout_reference_analysis.py"
EXAMPLE_PATH = ROOT / "examples" / "example-layout-reference-analysis.json"
VALID_FIXTURE = ROOT / "tests" / "fixtures" / "valid-layout-reference-analysis.json"
INVALID_FIXTURE = ROOT / "tests" / "fixtures" / "invalid-layout-reference-analysis.json"

SPEC = importlib.util.spec_from_file_location("a1_validator", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import setup guard
    raise RuntimeError("Unable to load A1 validator")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def load_valid() -> dict:
    return json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))


class LayoutReferenceValidatorTests(unittest.TestCase):
    def assert_has_error(self, errors: list[str], text: str) -> None:
        self.assertTrue(
            any(text.casefold() in error.casefold() for error in errors),
            f"Expected an error containing {text!r}, got: {errors}",
        )

    def test_valid_example_passes(self):
        self.assertEqual([], validator.validate_file(EXAMPLE_PATH))

    def test_valid_fixture_passes(self):
        self.assertEqual([], validator.validate_file(VALID_FIXTURE))

    def test_invalid_json_syntax_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "invalid.json"
            path.write_text("{not-json", encoding="utf-8")
            errors = validator.validate_file(path)
        self.assert_has_error(errors, "invalid JSON")

    def test_missing_required_field_fails(self):
        data = load_valid()
        del data["page"]
        self.assert_has_error(validator.validate_document(data), "required")

    def test_confidence_out_of_range_fails(self):
        data = load_valid()
        data["page"]["confidence"] = 1.1
        self.assert_has_error(validator.validate_document(data), "$.page.confidence")

    def test_duplicate_region_id_fails(self):
        data = load_valid()
        duplicate = copy.deepcopy(data["regions"][0])
        duplicate["label"] = "重复区域"
        data["regions"].append(duplicate)
        self.assert_has_error(validator.validate_document(data), "duplicate ID")

    def test_missing_parent_region_fails(self):
        data = load_valid()
        data["regions"][0]["parent_region_id"] = "missing_parent"
        self.assert_has_error(validator.validate_document(data), "missing parent")

    def test_relationship_missing_region_fails(self):
        data = load_valid()
        data["region_relationships"].append(
            {
                "relationship_id": "bad_relationship",
                "source_region_id": "main_content",
                "target_region_id": "missing_region",
                "relationship_type": "adjacent-to",
                "description": "测试不存在的目标引用。",
                "evidence_level": "observed",
                "confidence": 0.8,
            }
        )
        self.assert_has_error(validator.validate_document(data), "missing region")

    def test_component_group_missing_region_fails(self):
        data = load_valid()
        data["component_groups"][0]["region_id"] = "missing_region"
        self.assert_has_error(validator.validate_document(data), "missing region")

    def test_forbidden_field_fails_case_insensitively(self):
        data = load_valid()
        data["MODEL"] = "text inside values is not scanned"
        self.assert_has_error(validator.validate_document(data), "forbidden field")

    def test_text_value_containing_model_is_allowed(self):
        data = load_valid()
        data["notes"] = ["The word model in natural language is allowed."]
        self.assertEqual([], validator.validate_document(data))

    def test_validator_success_exit_code_is_zero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(VALID_FIXTURE)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Valid layout reference analysis", result.stdout)

    def test_validator_failure_exit_code_is_nonzero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(INVALID_FIXTURE)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Validation failed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
