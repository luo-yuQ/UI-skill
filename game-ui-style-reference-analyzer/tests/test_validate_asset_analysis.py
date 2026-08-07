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
SCRIPT_PATH = ROOT / "scripts" / "validate_asset_analysis.py"
SCHEMA_PATH = ROOT / "schemas" / "asset-analysis.schema.json"
EXAMPLE_PATH = ROOT / "examples" / "b1-single-reference-analysis.json"

SPEC = importlib.util.spec_from_file_location("b1_validator", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import setup guard
    raise RuntimeError("Unable to load B1 validator")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def load_valid() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


class AssetAnalysisValidatorTests(unittest.TestCase):
    def assert_has_error(self, errors: list[str], text: str) -> None:
        self.assertTrue(
            any(text.casefold() in error.casefold() for error in errors),
            f"Expected an error containing {text!r}, got: {errors}",
        )

    def test_schema_is_valid_json_object(self):
        schema = validator.load_schema(SCHEMA_PATH)
        self.assertIsInstance(schema, dict)
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual([], validator.check_schema_definition(schema))

    def test_new_example_passes(self):
        self.assertEqual([], validator.validate_file(EXAMPLE_PATH))

    def test_standard_output_without_user_intended_use_is_valid(self):
        data = load_valid()
        self.assertNotIn("user_intended_use", data)
        self.assertEqual([], validator.validate_document(data))

    def test_explicit_user_intended_use_is_valid(self):
        data = load_valid()
        data["user_intended_use"] = "style_reference"
        self.assertEqual([], validator.validate_document(data))

    def test_standard_output_without_layout_behavior_is_valid(self):
        data = load_valid()
        self.assertNotIn("layout_behavior", data)
        self.assertEqual([], validator.validate_document(data))

    def test_standard_output_without_laya_new_ui_is_valid(self):
        data = load_valid()
        self.assertNotIn("laya_new_ui", data)
        self.assertEqual([], validator.validate_document(data))

    def test_standard_output_without_role_candidates_is_valid(self):
        data = load_valid()
        self.assertNotIn("role_candidates", data)
        self.assertEqual([], validator.validate_document(data))

    def test_standard_output_without_intended_role_is_valid(self):
        data = load_valid()
        self.assertNotIn("intended_role", data)
        self.assertEqual([], validator.validate_document(data))

    def test_missing_required_field_fails(self):
        data = load_valid()
        del data["visual_language"]
        self.assert_has_error(validator.validate_document(data), "visual_language")

    def test_invalid_reference_kind_fails(self):
        data = load_valid()
        data["reference_kind"] = "gamewide_final_style"
        self.assert_has_error(validator.validate_document(data), "reference_kind")

    def test_visual_language_structure_error_fails(self):
        data = load_valid()
        data["visual_language"]["color"] = "blue-gray"
        self.assert_has_error(validator.validate_document(data), "visual_language.color")

    def test_style_candidates_structure_error_fails(self):
        data = load_valid()
        data["style_candidates"] = [{"trait": "cool palette"}]
        errors = validator.validate_document(data)
        self.assert_has_error(errors, "style_candidates")
        self.assert_has_error(errors, "evidence")

    def test_non_tangible_effects_fail_in_material(self):
        for effect in ("fire", "smoke", "fog", "glow", "particles", "emissive", "烟雾"):
            with self.subTest(effect=effect):
                data = load_valid()
                data["visual_language"]["material"]["overall_tendencies"] = [effect]
                self.assert_has_error(
                    validator.validate_document(data),
                    "forbidden in Material Language",
                )

    def test_recommendation_language_fails_in_inferred_evidence(self):
        data = load_valid()
        data["evidence"][1]["statement"] = "This treatment is recommended for a login screen."
        self.assert_has_error(
            validator.validate_document(data),
            "recommendation language is forbidden",
        )

    def test_usage_language_fails_in_any_string_field(self):
        for phrase in ("This can be used as a button frame.", "这个适合用于按钮边框。"):
            with self.subTest(phrase=phrase):
                data = load_valid()
                data["notes"] = [phrase]
                self.assert_has_error(
                    validator.validate_document(data),
                    "recommendation language is forbidden",
                )

    def test_page_spatial_organization_fails_as_style_candidate(self):
        for trait in ("upper illustration plus lower panel", "上方插画 + 下方面板"):
            with self.subTest(trait=trait):
                data = load_valid()
                data["style_candidates"][0]["trait"] = trait
                self.assert_has_error(
                    validator.validate_document(data),
                    "spatial organization is forbidden",
                )

    def test_composition_fails_as_style_candidate(self):
        data = load_valid()
        data["style_candidates"][0]["trait"] = "centered composition"
        self.assert_has_error(
            validator.validate_document(data),
            "spatial organization is forbidden",
        )

    def test_spatial_fact_remains_valid_in_visual_description(self):
        data = load_valid()
        data["visual_description"] = "An upper illustration appears above a lower panel."
        self.assertEqual([], validator.validate_document(data))

    def test_confidence_out_of_range_fails(self):
        data = load_valid()
        data["confidence"] = 1.1
        self.assert_has_error(validator.validate_document(data), "$.confidence")

    def test_invalid_json_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "invalid.json"
            path.write_text("{not-json", encoding="utf-8")
            errors = validator.validate_file(path)
        self.assert_has_error(errors, "invalid JSON")

    def test_content_specific_traits_are_valid(self):
        data = load_valid()
        data["content_specific_traits"].append(
            {
                "trait": "the current red banner",
                "evidence": [
                    {
                        "source": "observed",
                        "statement": "One red banner appears behind the subject.",
                        "confidence": 0.95,
                    }
                ],
                "confidence": 0.94,
            }
        )
        self.assertEqual([], validator.validate_document(data))

    def test_uncertainties_are_valid(self):
        data = load_valid()
        data["uncertainties"].append(
            {
                "topic": "surface material",
                "reason": "Compression obscures whether the surface is metal or stone.",
            }
        )
        self.assertEqual([], validator.validate_document(data))

    def test_legacy_compatibility_fields_remain_valid(self):
        data = load_valid()
        data["input_kind"] = "single_asset"
        data["schema_version"] = "0.1"
        data["intended_role"] = "legacy_background_reference"
        data["file_path"] = "assets/reference.png"
        data["technical_metadata"] = {
            "width": 1024,
            "height": 1024,
            "aspect_ratio": "1:1",
            "format": "png",
            "has_alpha": False,
        }
        data["role_candidates"] = ["background_reference"]
        data["layout_behavior"] = {
            "stretchable": False,
            "nine_slice_candidate": False,
            "recommended_scale_mode": "contain",
            "safe_overlay_note": "Legacy compatibility note.",
            "protected_region_note": "Legacy compatibility note.",
        }
        data["laya_new_ui"] = {
            "recommended_node_type": "Image",
            "size_grid_candidate": None,
            "usage_note": "Legacy compatibility note.",
        }
        data["user_intended_use"] = "direct_asset"
        self.assertEqual([], validator.validate_document(data))

    def test_cli_success_returns_zero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(EXAMPLE_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Valid B1 asset analysis", result.stdout)

    def test_cli_schema_failure_returns_nonzero(self):
        data = load_valid()
        del data["reference_kind"]
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "invalid-schema-instance.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Validation failed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
