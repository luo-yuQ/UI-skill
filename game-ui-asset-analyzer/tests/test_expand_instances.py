from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import render_instances_overlay as renderer  # noqa: E402
import validate_expand_instances as validator  # noqa: E402


def make_instances() -> dict:
    return {
        "instance_type": "peer status component",
        "repeat_count": 2,
        "instances": [
            {
                "id": "instance_001",
                "bbox": {"x": 30, "y": 40, "width": 240, "height": 120},
                "partial_instance": False,
                "confidence": 0.97,
            },
            {
                "id": "instance_002",
                "bbox": {"x": 290, "y": 38, "width": 245, "height": 124},
                "partial_instance": False,
                "confidence": 0.94,
            },
        ],
        "reason": "Two peers share one primary component structure.",
    }


class ExpandInstancesTests(unittest.TestCase):
    def make_image(self, size=(1024, 600), color="navy") -> Path:
        context = tempfile.TemporaryDirectory()
        image_path = Path(context.name) / "analysis-image.png"
        Image.new("RGB", size, color).save(image_path)
        self.addCleanup(context.cleanup)
        return image_path

    def assert_has_error(self, errors: list[str], text: str) -> None:
        self.assertTrue(
            any(text.casefold() in error.casefold() for error in errors),
            f"Expected an error containing {text!r}, got: {errors}",
        )

    def test_schema_is_valid_draft_2020_12(self):
        schema = validator.load_schema()
        self.assertEqual(
            "https://json-schema.org/draft/2020-12/schema",
            schema["$schema"],
        )
        Draft202012Validator.check_schema(schema)

    def test_single_instance_is_valid(self):
        data = make_instances()
        data["instances"] = data["instances"][:1]
        data["repeat_count"] = 1
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_multiple_instances_allow_size_spacing_and_overlap_variation(self):
        data = make_instances()
        data["instances"][1]["bbox"] = {
            "x": 250,
            "y": 70,
            "width": 310,
            "height": 145,
        }
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_partial_instance_true_is_valid(self):
        data = make_instances()
        data["instances"][1]["partial_instance"] = True
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_zero_repeat_count_with_empty_instances_is_contract_valid(self):
        data = make_instances()
        data["repeat_count"] = 0
        data["instances"] = []
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_repeat_count_mismatch_fails(self):
        data = make_instances()
        data["repeat_count"] = 3
        self.assert_has_error(
            validator.validate_document(data, self.make_image()),
            "does not match instances length 2",
        )

    def test_duplicate_instance_ids_fail(self):
        data = make_instances()
        data["instances"][1]["id"] = data["instances"][0]["id"]
        self.assert_has_error(
            validator.validate_document(data, self.make_image()),
            "duplicate instance id",
        )

    def test_non_positive_width_or_height_fails(self):
        for field, value in (
            ("width", 0),
            ("height", 0),
            ("width", -1),
            ("height", -1),
        ):
            with self.subTest(field=field, value=value):
                data = make_instances()
                data["instances"][0]["bbox"][field] = value
                self.assertNotEqual(
                    [],
                    validator.validate_document(data, self.make_image()),
                )

    def test_bbox_out_of_bounds_fails_without_mutation(self):
        data = make_instances()
        data["instances"][0]["bbox"] = {
            "x": 900,
            "y": 500,
            "width": 200,
            "height": 150,
        }
        original = copy.deepcopy(data)
        errors = validator.validate_document(data, self.make_image())
        self.assert_has_error(errors, "right edge 1100")
        self.assert_has_error(errors, "bottom edge 650")
        self.assertEqual(original, data)

    def test_confidence_out_of_bounds_fails(self):
        for value in (-0.01, 1.01):
            with self.subTest(value=value):
                data = make_instances()
                data["instances"][0]["confidence"] = value
                self.assertNotEqual(
                    [],
                    validator.validate_document(data, self.make_image()),
                )

    def test_missing_required_field_fails(self):
        data = make_instances()
        del data["instances"][0]["bbox"]
        self.assert_has_error(
            validator.validate_document(data, self.make_image()),
            "required property",
        )

    def test_partial_instance_must_be_boolean(self):
        data = make_instances()
        data["instances"][0]["partial_instance"] = "false"
        self.assert_has_error(
            validator.validate_document(data, self.make_image()),
            "is not of type 'boolean'",
        )

    def test_instance_type_must_not_be_empty_or_blank(self):
        for value in ("", "   "):
            with self.subTest(value=value):
                data = make_instances()
                data["instance_type"] = value
                self.assertNotEqual(
                    [],
                    validator.validate_document(data, self.make_image()),
                )

    def test_overlay_renderer_writes_image_without_changing_json(self):
        image_path = self.make_image()
        document = image_path.parent / "instances.json"
        output = image_path.parent / "instances-overlay.png"
        data = make_instances()
        data["instances"][1]["partial_instance"] = True
        document.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        original_json = document.read_bytes()

        renderer.render_overlay(image_path, document, output)

        self.assertTrue(output.is_file())
        self.assertEqual(original_json, document.read_bytes())
        with Image.open(image_path) as source, Image.open(output) as overlay:
            self.assertEqual(source.size, overlay.size)
            difference = ImageChops.difference(source.convert("RGB"), overlay)
            self.assertIsNotNone(difference.getbbox())

    def test_validator_and_overlay_cli_succeed(self):
        image_path = self.make_image()
        document = image_path.parent / "instances.json"
        output = image_path.parent / "instances-overlay.png"
        document.write_text(json.dumps(make_instances()), encoding="utf-8")

        validate_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "validate_expand_instances.py"),
                str(document),
                "--analysis-image",
                str(image_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        render_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "render_instances_overlay.py"),
                "--analysis-image",
                str(image_path),
                "--instances",
                str(document),
                "--output-image",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, validate_result.returncode, validate_result.stderr)
        self.assertIn("Valid expanded instances v0.1", validate_result.stdout)
        self.assertEqual(0, render_result.returncode, render_result.stderr)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
