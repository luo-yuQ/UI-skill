from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_extraction_request as builder  # noqa: E402


REQUEST_SCHEMA_PATH = ROOT / "schemas" / "extraction-request.schema.json"


def reviewed_doc() -> dict:
    return {
        "schema_version": "direct-assets-reviewed-v0.1",
        "source_image": "source.png",
        "source_image_size": {"width": 100, "height": 80},
        "analysis_image": "analysis-image.png",
        "analysis_image_size": {"width": 100, "height": 80},
        "assets": [
            {
                "id": "asset_001",
                "label": "background",
                "taxonomy": "background",
                "bbox_analysis": {"x": 0, "y": 0, "width": 100, "height": 80},
                "bbox_source": {"x": 0, "y": 0, "width": 100, "height": 80},
                "partial": False,
                "confidence": 0.9,
            },
            {
                "id": "asset_004",
                "label": "menu button",
                "taxonomy": "button",
                "bbox_analysis": {"x": 10, "y": 10, "width": 20, "height": 20},
                "bbox_source": {"x": 8, "y": 9, "width": 24, "height": 22},
                "partial": False,
                "confidence": 0.9,
                "review": {
                    "status": "bbox_modified",
                    "original_bbox_source": {
                        "x": 10,
                        "y": 10,
                        "width": 20,
                        "height": 20,
                    },
                },
            },
            {
                "id": "manual_002",
                "label": "manual_002",
                "taxonomy": "manual",
                "bbox_source": {"x": 4, "y": 5, "width": 10, "height": 12},
                "review_origin": "manual",
            },
        ],
        "review_summary": {
            "original_asset_count": 4,
            "bbox_modified_count": 1,
            "explicit_keep_count": 0,
            "dropped_count": 1,
            "manual_kept_count": 1,
            "manual_dropped_count": 0,
            "final_asset_count": 3,
            "dropped_asset_ids": ["asset_002"],
            "manual_dropped_asset_ids": [],
        },
    }


class MappingTests(unittest.TestCase):
    def test_happy_path_maps_reviewed_fields(self):
        request = builder.build_extraction_request(reviewed_doc())
        self.assertEqual("0.1", request["schema_version"])
        self.assertEqual("source.png", request["source_image"])
        self.assertEqual(3, len(request["assets"]))

        first = request["assets"][0]
        self.assertEqual("asset_001", first["asset_id"])
        self.assertEqual("background", first["asset_type"])
        self.assertEqual(
            {"x": 0, "y": 0, "width": 100, "height": 80}, first["final_bbox"]
        )
        self.assertEqual("direct_crop", first["extraction_mode"])

    def test_bbox_source_is_authoritative_not_bbox_analysis(self):
        request = builder.build_extraction_request(reviewed_doc())
        modified = request["assets"][1]
        self.assertEqual(
            {"x": 8, "y": 9, "width": 24, "height": 22}, modified["final_bbox"]
        )
        self.assertNotEqual(
            {"x": 10, "y": 10, "width": 20, "height": 20},
            modified["final_bbox"],
        )

    def test_taxonomy_outside_enum_maps_to_unknown(self):
        request = builder.build_extraction_request(reviewed_doc())
        self.assertEqual("unknown", request["assets"][2]["asset_type"])

    def test_extraction_mode_override_is_global(self):
        request = builder.build_extraction_request(
            reviewed_doc(), extraction_mode="foreground_extract"
        )
        self.assertTrue(
            all(
                asset["extraction_mode"] == "foreground_extract"
                for asset in request["assets"]
            )
        )

    def test_mapping_is_pure_and_does_not_mutate_input(self):
        document = reviewed_doc()
        frozen = copy.deepcopy(document)
        builder.build_extraction_request(document)
        self.assertEqual(frozen, document)


class ValidationTests(unittest.TestCase):
    def test_wrong_schema_version_is_rejected(self):
        document = reviewed_doc()
        document["schema_version"] = "direct-assets-reviewed-v0.2"
        with self.assertRaises(builder.ExtractionRequestBuildError):
            builder.build_extraction_request(document)

    def test_empty_assets_list_is_rejected(self):
        document = reviewed_doc()
        document["assets"] = []
        with self.assertRaises(builder.ExtractionRequestBuildError):
            builder.build_extraction_request(document)

    def test_asset_id_pattern_is_enforced_not_rewritten(self):
        document = reviewed_doc()
        document["assets"][0]["id"] = "Asset 001"
        with self.assertRaises(builder.ExtractionRequestBuildError):
            builder.build_extraction_request(document)

    def test_duplicate_asset_ids_are_rejected(self):
        document = reviewed_doc()
        document["assets"].append(copy.deepcopy(document["assets"][0]))
        with self.assertRaises(builder.ExtractionRequestBuildError):
            builder.build_extraction_request(document)

    def test_missing_bbox_source_is_rejected(self):
        document = reviewed_doc()
        del document["assets"][2]["bbox_source"]
        with self.assertRaises(builder.ExtractionRequestBuildError):
            builder.build_extraction_request(document)

    def test_invalid_bbox_values_are_rejected(self):
        document = reviewed_doc()
        document["assets"][0]["bbox_source"]["width"] = 0
        with self.assertRaises(builder.ExtractionRequestBuildError):
            builder.build_extraction_request(document)

    def test_invalid_extraction_mode_is_rejected(self):
        with self.assertRaises(builder.ExtractionRequestBuildError):
            builder.build_extraction_request(
                reviewed_doc(), extraction_mode="magic_mode"
            )


class SchemaCompatTests(unittest.TestCase):
    def test_output_validates_against_extraction_request_schema(self):
        schema = json.loads(REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        request = builder.build_extraction_request(reviewed_doc())
        Draft202012Validator(schema).validate(request)

    def test_output_of_golden_style_manual_asset_validates(self):
        schema = json.loads(REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        asset_schema = {
            "$schema": schema["$schema"],
            "$ref": "#/$defs/asset",
            "$defs": schema["$defs"],
        }
        validator = Draft202012Validator(asset_schema)
        request = builder.build_extraction_request(reviewed_doc())
        for asset in request["assets"]:
            validator.validate(asset)


class CliTests(unittest.TestCase):
    def test_cli_writes_output_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reviewed_path = tmp_path / "reviewed-direct-assets.json"
            output_path = tmp_path / "extraction-request.json"
            reviewed_path.write_text(
                json.dumps(reviewed_doc()), encoding="utf-8"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_extraction_request.py"),
                    "--reviewed-json",
                    str(reviewed_path),
                    "--output-json",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            request = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("0.1", request["schema_version"])
            self.assertEqual(3, len(request["assets"]))

    def test_cli_rejects_bad_document_with_exit_code_2(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reviewed_path = tmp_path / "reviewed-direct-assets.json"
            output_path = tmp_path / "extraction-request.json"
            document = reviewed_doc()
            document["schema_version"] = "nope"
            reviewed_path.write_text(json.dumps(document), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_extraction_request.py"),
                    "--reviewed-json",
                    str(reviewed_path),
                    "--output-json",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
