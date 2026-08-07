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
SCRIPT_PATH = ROOT / "scripts" / "validate_style_profile.py"
SCHEMA_PATH = ROOT / "schemas" / "style-profile.schema.json"
EXAMPLE_PATH = ROOT / "examples" / "b2-style-profile.json"

SPEC = importlib.util.spec_from_file_location("b2_validator", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import setup guard
    raise RuntimeError("Unable to load B2 validator")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def load_valid() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


class StyleProfileValidatorTests(unittest.TestCase):
    def assert_has_error(self, errors: list[str], text: str) -> None:
        self.assertTrue(
            any(text.casefold() in error.casefold() for error in errors),
            f"Expected an error containing {text!r}, got: {errors}",
        )

    def test_schema_is_valid_json_object(self):
        schema = validator.load_schema(SCHEMA_PATH)
        self.assertIsInstance(schema, dict)
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual([], validator.b1_validator.check_schema_definition(schema))

    def test_formal_example_passes(self):
        self.assertEqual([], validator.validate_file(EXAMPLE_PATH))

    def test_missing_required_top_level_field_fails(self):
        data = load_valid()
        del data["profile_id"]
        self.assert_has_error(validator.validate_document(data), "profile_id")

    def test_missing_source_analyses_fails(self):
        data = load_valid()
        del data["source_analyses"]
        self.assert_has_error(validator.validate_document(data), "source_analyses")

    def test_fewer_than_two_sources_fails(self):
        data = load_valid()
        data["source_analyses"] = data["source_analyses"][:1]
        self.assert_has_error(validator.validate_document(data), "at least 2")

    def test_invalid_classification_fails(self):
        data = load_valid()
        trait = data["visual_profiles"]["color_profile"]["stable"][0]
        trait["classification"] = "secondary"
        self.assert_has_error(validator.validate_document(data), "classification")

    def test_overall_confidence_below_zero_fails(self):
        data = load_valid()
        data["overall_confidence"] = -0.01
        self.assert_has_error(validator.validate_document(data), "overall_confidence")

    def test_overall_confidence_above_one_fails(self):
        data = load_valid()
        data["overall_confidence"] = 1.01
        self.assert_has_error(validator.validate_document(data), "overall_confidence")

    def test_trait_confidence_out_of_range_fails(self):
        data = load_valid()
        data["visual_profiles"]["shape_profile"]["stable"][0]["confidence"] = 1.1
        self.assert_has_error(validator.validate_document(data), "confidence")

    def test_supporting_references_structure_error_fails(self):
        data = load_valid()
        data["visual_profiles"]["color_profile"]["stable"][0][
            "supporting_references"
        ] = "knight_character_01"
        self.assert_has_error(validator.validate_document(data), "supporting_references")

    def test_conflicting_trait_is_valid(self):
        data = load_valid()
        conflicts = data["visual_profiles"]["rendering_profile"]["conflicting"]
        self.assertEqual(1, len(conflicts))
        self.assertEqual("conflicting", conflicts[0]["classification"])
        self.assertEqual([], validator.validate_document(data))

    def test_uncertain_trait_is_valid(self):
        data = load_valid()
        uncertain = data["visual_profiles"]["world_visual_profile"]["uncertain"]
        self.assertEqual(1, len(uncertain))
        self.assertEqual("uncertain", uncertain[0]["classification"])
        self.assertEqual([], validator.validate_document(data))

    def test_empty_optional_classifications_are_valid(self):
        data = load_valid()
        profile = data["visual_profiles"]["decoration_profile"]
        for classification in ("stable", "secondary", "local", "conflicting", "uncertain"):
            profile[classification] = []
        self.assertEqual([], validator.validate_document(data))

    def test_invalid_json_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "invalid.json"
            path.write_text("{not-json", encoding="utf-8")
            errors = validator.validate_file(path)
        self.assert_has_error(errors, "invalid JSON")

    def test_duplicate_source_ids_fail(self):
        data = load_valid()
        data["source_analyses"][1]["asset_id"] = data["source_analyses"][0]["asset_id"]
        self.assert_has_error(validator.validate_document(data), "duplicate source asset_id")

    def test_valid_provenance_passes(self):
        data = load_valid()
        known = {item["asset_id"] for item in data["source_analyses"]}
        trait = data["visual_profiles"]["color_profile"]["stable"][0]
        self.assertTrue(set(trait["supporting_references"]).issubset(known))
        self.assertEqual([], validator.validate_document(data))

    def test_unknown_provenance_reference_fails(self):
        data = load_valid()
        data["visual_profiles"]["color_profile"]["stable"][0][
            "supporting_references"
        ] = ["missing_analysis"]
        self.assert_has_error(validator.validate_document(data), "unknown B1 asset_id")

    def test_duplicate_trait_id_fails(self):
        data = load_valid()
        first_id = data["visual_profiles"]["color_profile"]["stable"][0]["trait_id"]
        data["visual_profiles"]["shape_profile"]["stable"][0]["trait_id"] = first_id
        self.assert_has_error(validator.validate_document(data), "duplicate trait_id")

    def test_user_group_context_is_optional(self):
        data = load_valid()
        self.assertNotIn("user_group_context", data)
        self.assertEqual([], validator.validate_document(data))

    def test_valid_user_group_context_passes(self):
        data = load_valid()
        data["user_group_context"] = {
            "source": "user_provided",
            "statement": "The character reference is older than the environment and UI references.",
            "affected_references": ["knight_character_01"],
            "weighting_effect": "The older reference retains provenance but contributes less to current-direction interpretation."
        }
        self.assertEqual([], validator.validate_document(data))

    def test_unresolved_conflict_is_valid(self):
        data = load_valid()
        conflict = data["unresolved_conflicts"][0]
        self.assertEqual("unresolved", conflict["status"])
        self.assertEqual([], validator.validate_document(data))

    def test_recommendation_language_fails(self):
        data = load_valid()
        data["notes"] = ["This can be used as a button style."]
        self.assert_has_error(validator.validate_document(data), "recommendation language")

    def test_cli_success_returns_zero(self):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT_PATH), str(EXAMPLE_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Valid B2 style profile", result.stdout)

    def test_cli_failure_returns_nonzero(self):
        data = load_valid()
        del data["overall_visual_identity"]
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "invalid-profile.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(SCRIPT_PATH), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Validation failed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
