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
REPOSITORY_ROOT = ROOT.parent
SCRIPT_PATH = ROOT / "scripts" / "validate_layout_reference_review.py"
EXAMPLE_REVIEW = ROOT / "examples" / "example-layout-reference-review.json"
EXAMPLE_DRAFT = ROOT / "examples" / "example-draft-analysis.json"
EXAMPLE_FINAL = ROOT / "examples" / "example-final-analysis.json"
VALID_FIXTURE = ROOT / "tests" / "fixtures" / "valid-layout-reference-review.json"
INVALID_FIXTURE = ROOT / "tests" / "fixtures" / "invalid-layout-reference-review.json"
A1_VALIDATOR_PATH = (
    REPOSITORY_ROOT
    / "game-ui-layout-reference-analyzer"
    / "scripts"
    / "validate_layout_reference_analysis.py"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import setup guard
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_module("a2_validator", SCRIPT_PATH)
a1_validator = load_module("a1_validator_for_a2_tests", A1_VALIDATOR_PATH)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def consistency_review() -> dict:
    review = load_json(VALID_FIXTURE)
    review["source_analysis"]["analysis_id"] = "sky-vanguard-shop-draft-001"
    review["finalization"]["final_analysis_id"] = "sky-vanguard-shop-final-001"
    return review


def add_finding(
    review: dict,
    finding_id: str,
    action: str,
    entity_id: str,
    *,
    severity: str = "minor",
) -> None:
    review["findings"].append(
        {
            "finding_id": finding_id,
            "error_type": "other",
            "severity": severity,
            "correction_action": action,
            "affected_entities": [entity_id],
            "description": "动态一致性测试 finding。",
            "screenshot_evidence": "测试截图证据。",
            "rationale": "用于验证结构化引用规则。",
            "proposed_change": "按测试动作处理实体。",
            "confidence": 0.8,
        }
    )
    review["review_summary"]["issue_count"] = len(review["findings"])
    review["review_summary"][f"{severity}_issue_count"] += 1
    review["review_summary"]["verdict"] = (
        "approved_with_major_corrections"
        if severity in {"critical", "major"}
        else "approved_with_minor_corrections"
    )
    if action in validator.CHANGE_ACTIONS:
        review["review_summary"]["changes_applied"] = True
        review["finalization"]["applied_finding_ids"].append(finding_id)


class LayoutReferenceReviewValidatorTests(unittest.TestCase):
    def assert_has_error(self, errors: list[str], text: str) -> None:
        self.assertTrue(
            any(text.casefold() in error.casefold() for error in errors),
            f"Expected an error containing {text!r}, got: {errors}",
        )

    def test_valid_example_review_passes(self):
        self.assertEqual([], validator.validate_file(EXAMPLE_REVIEW))

    def test_valid_fixture_passes(self):
        self.assertEqual([], validator.validate_file(VALID_FIXTURE))

    def test_full_example_consistency_passes(self):
        self.assertEqual(
            [],
            validator.validate_file(
                EXAMPLE_REVIEW,
                draft_path=EXAMPLE_DRAFT,
                final_path=EXAMPLE_FINAL,
            ),
        )

    def test_invalid_json_syntax_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "bad.json"
            path.write_text("{bad-json", encoding="utf-8")
            errors = validator.validate_file(path)
        self.assert_has_error(errors, "invalid review JSON")

    def test_missing_required_field_fails(self):
        review = load_json(VALID_FIXTURE)
        del review["source"]
        self.assert_has_error(validator.validate_document(review), "required")

    def test_duplicate_finding_id_fails(self):
        review = load_json(INVALID_FIXTURE)
        self.assert_has_error(validator.validate_document(review), "duplicate value")

    def test_issue_count_mismatch_fails(self):
        review = load_json(VALID_FIXTURE)
        review["review_summary"]["issue_count"] = 1
        self.assert_has_error(validator.validate_document(review), "issue_count")

    def test_severity_count_mismatch_fails(self):
        review = load_json(VALID_FIXTURE)
        review["review_summary"]["major_issue_count"] = 1
        self.assert_has_error(validator.validate_document(review), "major_issue_count")

    def test_unknown_applied_finding_id_fails(self):
        review = load_json(VALID_FIXTURE)
        review["finalization"]["applied_finding_ids"] = ["missing_finding"]
        self.assert_has_error(validator.validate_document(review), "unknown finding IDs")

    def test_approved_with_major_finding_fails(self):
        review = load_json(VALID_FIXTURE)
        add_finding(review, "major_finding", "modified", "page", severity="major")
        review["review_summary"]["verdict"] = "approved"
        self.assert_has_error(validator.validate_document(review), "approved cannot")

    def test_rejected_ready_for_downstream_fails(self):
        review = load_json(VALID_FIXTURE)
        review["review_summary"]["verdict"] = "rejected"
        self.assert_has_error(validator.validate_document(review), "rejected review")

    def test_confidence_out_of_range_fails(self):
        review = load_json(VALID_FIXTURE)
        review["review_summary"]["review_confidence"] = 1.1
        self.assert_has_error(validator.validate_document(review), "review_confidence")

    def test_forbidden_field_fails_case_insensitively(self):
        review = load_json(VALID_FIXTURE)
        review["IMAGE_PROMPT"] = "string values are not scanned"
        self.assert_has_error(validator.validate_document(review), "forbidden field")

    def test_string_value_with_engine_word_is_allowed(self):
        review = load_json(VALID_FIXTURE)
        review["notes"] = ["Unity in natural language is not a key."]
        self.assertEqual([], validator.validate_document(review))

    def test_draft_source_analysis_id_mismatch_fails(self):
        review = consistency_review()
        review["source_analysis"]["analysis_id"] = "wrong-draft-id"
        errors = validator.validate_document(review, draft=load_json(EXAMPLE_DRAFT))
        self.assert_has_error(errors, "does not match draft analysis_id")

    def test_removed_finding_missing_draft_entity_fails(self):
        review = consistency_review()
        add_finding(review, "remove_missing", "removed", "not_in_draft")
        errors = validator.validate_document(review, draft=load_json(EXAMPLE_DRAFT))
        self.assert_has_error(errors, "missing from draft")

    def test_final_analysis_id_mismatch_fails(self):
        review = consistency_review()
        review["finalization"]["final_analysis_id"] = "wrong-final-id"
        errors = validator.validate_document(review, final=load_json(EXAMPLE_FINAL))
        self.assert_has_error(errors, "does not match final analysis_id")

    def test_example_final_passes_a1_validator(self):
        self.assertEqual([], a1_validator.validate_file(EXAMPLE_FINAL))

    def test_removed_entity_still_in_final_fails(self):
        review = consistency_review()
        add_finding(review, "remove_category_tabs", "removed", "category_tabs")
        errors = validator.validate_document(review, final=load_json(EXAMPLE_FINAL))
        self.assert_has_error(errors, "still exists in final")

    def test_added_entity_missing_from_final_fails(self):
        review = consistency_review()
        add_finding(review, "add_missing_region", "added", "new_missing_region")
        errors = validator.validate_document(review, final=load_json(EXAMPLE_FINAL))
        self.assert_has_error(errors, "is missing from final")

    def test_validator_success_exit_code_is_zero(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                str(EXAMPLE_REVIEW),
                "--draft",
                str(EXAMPLE_DRAFT),
                "--final",
                str(EXAMPLE_FINAL),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Valid layout reference review", result.stdout)

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
