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

import prepare_analysis_input as preparer  # noqa: E402
import render_structural_overlay as renderer  # noqa: E402
import validate_structural_split as validator  # noqa: E402


def make_split() -> dict:
    return {
        "no_useful_structural_split": False,
        "children": [
            {
                "id": "child_001",
                "label": "navigation and identity",
                "bbox": {"x": 20, "y": 30, "width": 280, "height": 120},
                "confidence": 0.96,
            },
            {
                "id": "child_002",
                "label": "resource status collection",
                "bbox": {"x": 680, "y": 25, "width": 320, "height": 135},
                "confidence": 0.93,
            },
        ],
        "reason": "Two stable regions have different responsibilities.",
    }


class StructuralSplitTests(unittest.TestCase):
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

    def test_multiple_children_are_valid_and_overlap_is_allowed(self):
        data = make_split()
        data["children"][1]["bbox"] = {
            "x": 250,
            "y": 100,
            "width": 400,
            "height": 200,
        }
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_single_child_is_valid(self):
        data = make_split()
        data["children"] = data["children"][:1]
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_no_useful_structural_split_is_valid(self):
        data = {
            "no_useful_structural_split": True,
            "children": [],
            "reason": "No child would materially reduce visual complexity.",
        }
        self.assertEqual([], validator.validate_document(data, self.make_image()))

    def test_true_with_children_fails(self):
        data = make_split()
        data["no_useful_structural_split"] = True
        self.assert_has_error(
            validator.validate_document(data, self.make_image()),
            "expected to be empty",
        )

    def test_false_with_empty_children_fails(self):
        data = make_split()
        data["children"] = []
        self.assert_has_error(
            validator.validate_document(data, self.make_image()),
            "non-empty",
        )

    def test_bbox_out_of_bounds_fails_without_mutation(self):
        data = make_split()
        data["children"][0]["bbox"] = {
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

    def test_non_positive_width_or_height_fails(self):
        for field, value in (("width", 0), ("height", 0), ("width", -1), ("height", -1)):
            with self.subTest(field=field, value=value):
                data = make_split()
                data["children"][0]["bbox"][field] = value
                self.assertNotEqual(
                    [],
                    validator.validate_document(data, self.make_image()),
                )

    def test_confidence_out_of_bounds_fails(self):
        for value in (-0.01, 1.01):
            with self.subTest(value=value):
                data = make_split()
                data["children"][0]["confidence"] = value
                self.assertNotEqual(
                    [],
                    validator.validate_document(data, self.make_image()),
                )

    def test_missing_required_field_fails(self):
        data = make_split()
        del data["children"][0]["label"]
        self.assert_has_error(
            validator.validate_document(data, self.make_image()),
            "required property",
        )

    def test_duplicate_child_ids_fail(self):
        data = make_split()
        data["children"][1]["id"] = data["children"][0]["id"]
        self.assert_has_error(
            validator.validate_document(data, self.make_image()),
            "duplicate child id",
        )

    def test_overlay_renderer_writes_image_without_changing_json(self):
        image_path = self.make_image()
        document = image_path.parent / "structural-split.json"
        output = image_path.parent / "structural-overlay.png"
        document.write_text(
            json.dumps(make_split(), ensure_ascii=False, indent=2) + "\n",
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
        document = image_path.parent / "structural-split.json"
        output = image_path.parent / "structural-overlay.png"
        document.write_text(json.dumps(make_split()), encoding="utf-8")

        validate_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "validate_structural_split.py"),
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
                str(SCRIPTS / "render_structural_overlay.py"),
                "--analysis-image",
                str(image_path),
                "--structural-split",
                str(document),
                "--output-image",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, validate_result.returncode, validate_result.stderr)
        self.assertIn("Valid structural split v0.1", validate_result.stdout)
        self.assertEqual(0, render_result.returncode, render_result.stderr)
        self.assertTrue(output.is_file())

    def test_shared_preparer_produces_deterministic_analysis_dimensions(self):
        context = tempfile.TemporaryDirectory()
        self.addCleanup(context.cleanup)
        temp = Path(context.name)
        source = temp / "node-crop.png"
        Image.new("RGB", (512, 300), "black").save(source)
        sizes = []
        for index in range(2):
            output = temp / f"analysis-{index}.png"
            metadata = temp / f"analysis-{index}.json"
            result = preparer.prepare_analysis_input(
                source,
                output,
                metadata,
                max_width=1024,
                force_width=True,
            )
            sizes.append(result["analysis_size"])
            with Image.open(output) as image:
                self.assertEqual((1024, 600), image.size)
        self.assertEqual(sizes[0], sizes[1])


if __name__ == "__main__":
    unittest.main()
