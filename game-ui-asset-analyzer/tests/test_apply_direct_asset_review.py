from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import apply_direct_asset_review as apply_review


def make_direct_asset(asset_id: str, bbox: dict[str, int]) -> dict[str, Any]:
    return {
        "id": asset_id,
        "label": f"label {asset_id}",
        "taxonomy": "icon",
        "bbox_analysis": dict(bbox),
        "bbox_source": dict(bbox),
        "partial": False,
        "confidence": 0.9,
    }


def make_assets_document() -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "source_image": "source.png",
        "source_image_size": {"width": 100, "height": 100},
        "analysis_image": "analysis-image.png",
        "analysis_image_size": {"width": 100, "height": 100},
        "assets": [
            make_direct_asset("asset_001", {"x": 10, "y": 10, "width": 20, "height": 20}),
            make_direct_asset("asset_002", {"x": 40, "y": 40, "width": 30, "height": 30}),
            make_direct_asset("asset_003", {"x": 5, "y": 60, "width": 15, "height": 15}),
        ],
    }


def make_overrides_document() -> dict[str, Any]:
    return {
        "schema_version": apply_review.OVERRIDES_SCHEMA_VERSION,
        "source_assets_json": "direct-assets.json",
        "image_size": {"width": 100, "height": 100},
        "overrides": {},
        "manual_assets": [],
    }


def run_apply(
    assets: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
):
    return apply_review.apply_review(
        assets or make_assets_document(),
        overrides or make_overrides_document(),
        "direct-assets.json",
        "review-overrides.json",
    )


class CleanInputValidationTests(unittest.TestCase):
    def test_no_overrides_reproduces_original_assets(self):
        document = make_assets_document()
        reviewed = run_apply(document)
        self.assertEqual(document["assets"], reviewed["assets"])
        self.assertEqual(3, reviewed["review_summary"]["final_asset_count"])

    def test_source_assets_json_basename_mismatch_fails(self):
        overrides = make_overrides_document()
        overrides["source_assets_json"] = "other-direct-assets.json"
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("basename", str(context.exception))

    def test_image_size_mismatch_fails_without_scaling(self):
        overrides = make_overrides_document()
        overrides["image_size"] = {"width": 200, "height": 200}
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("bbox scaling is forbidden", str(context.exception))

    def test_unsupported_overrides_schema_version_fails(self):
        overrides = make_overrides_document()
        overrides["schema_version"] = "future-version"
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("unsupported review-overrides.json schema_version", str(context.exception))

    def test_duplicate_original_asset_ids_fail(self):
        document = make_assets_document()
        document["assets"][1]["id"] = document["assets"][0]["id"]
        with self.assertRaises(ValueError) as context:
            run_apply(document)
        self.assertIn("duplicate original asset id", str(context.exception))

    def test_original_asset_out_of_bounds_bbox_fails(self):
        document = make_assets_document()
        document["assets"][0]["bbox_source"] = {"x": 90, "y": 90, "width": 20, "height": 20}
        with self.assertRaises(ValueError) as context:
            run_apply(document)
        self.assertIn("outside source image bounds", str(context.exception))

    def test_output_must_not_overwrite_inputs(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            assets_path = directory / "direct-assets.json"
            overrides_path = directory / "review-overrides.json"
            assets_path.write_text("{}", encoding="utf-8")
            overrides_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                apply_review.main(
                    [
                        "--assets-json",
                        str(assets_path),
                        "--overrides-json",
                        str(overrides_path),
                        "--output-json",
                        str(assets_path),
                    ]
                )


class BboxCoordinateContractTests(unittest.TestCase):
    def test_bbox_override_must_have_exactly_four_integer_keys(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {
            "asset_001": {"bbox": {"x": 1, "y": 2, "width": 5, "height": 5, "extra": 1}}
        }
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("exactly x/y/width/height", str(context.exception))

    def test_bbox_override_non_integer_fails(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {
            "asset_001": {"bbox": {"x": 1.5, "y": 2, "width": 5, "height": 5}}
        }
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("must be an integer", str(context.exception))

    def test_bbox_override_non_positive_size_fails(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {
            "asset_001": {"bbox": {"x": 1, "y": 2, "width": 0, "height": 5}}
        }
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("must be > 0", str(context.exception))

    def test_bbox_override_out_of_bounds_fails(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {
            "asset_001": {"bbox": {"x": 95, "y": 2, "width": 10, "height": 5}}
        }
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("outside source image bounds", str(context.exception))

    def test_bbox_override_is_never_scaled(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {
            "asset_001": {"bbox": {"x": 12, "y": 14, "width": 22, "height": 24}}
        }
        reviewed = run_apply(overrides=overrides)
        self.assertEqual(
            {"x": 12, "y": 14, "width": 22, "height": 24},
            reviewed["assets"][0]["bbox_source"],
        )
        self.assertEqual(
            {"x": 10, "y": 10, "width": 20, "height": 20},
            reviewed["assets"][0]["review"]["original_bbox_source"],
        )
        self.assertEqual("bbox_modified", reviewed["assets"][0]["review"]["status"])


class ReviewOverrideApplicationTests(unittest.TestCase):
    def test_unknown_override_asset_id_fails_fast(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {"asset_999": {"decision": "DROP"}}
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("unknown asset id(s): asset_999", str(context.exception))

    def test_unknown_override_field_fails_fast(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {"asset_001": {"taxonomy": "button"}}
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("unknown fields", str(context.exception))

    def test_invalid_decision_fails_fast(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {"asset_001": {"decision": "MAYBE"}}
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("must be KEEP or DROP", str(context.exception))

    def test_delete_override_drops_asset_and_records_id(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {"asset_002": {"decision": "DROP"}}
        reviewed = run_apply(overrides=overrides)
        remaining = [asset["id"] for asset in reviewed["assets"]]
        self.assertEqual(["asset_001", "asset_003"], remaining)
        self.assertEqual(1, reviewed["review_summary"]["dropped_count"])
        self.assertEqual(["asset_002"], reviewed["review_summary"]["dropped_asset_ids"])

    def test_bbox_override_records_review_status(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {
            "asset_001": {
                "bbox": {"x": 12, "y": 14, "width": 22, "height": 24},
                "decision": "KEEP",
            }
        }
        reviewed = run_apply(overrides=overrides)
        review = reviewed["assets"][0]["review"]
        self.assertEqual("bbox_modified_and_kept", review["status"])
        self.assertEqual(1, reviewed["review_summary"]["bbox_modified_count"])
        self.assertEqual(1, reviewed["review_summary"]["explicit_keep_count"])

    def test_explicit_keep_records_status_without_bbox_change(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {"asset_003": {"decision": "KEEP"}}
        reviewed = run_apply(overrides=overrides)
        self.assertEqual("explicit_keep", reviewed["assets"][2]["review"]["status"])
        self.assertNotIn("review", reviewed["assets"][0])
        self.assertNotIn("review", reviewed["assets"][1])


class ManualAssetTests(unittest.TestCase):
    def test_manual_asset_is_kept_with_manual_taxonomy(self):
        overrides = make_overrides_document()
        overrides["manual_assets"] = [
            {
                "asset_ref": "manual_001",
                "bbox": {"x": 70, "y": 70, "width": 10, "height": 10},
            }
        ]
        reviewed = run_apply(overrides=overrides)
        manual = reviewed["assets"][-1]
        self.assertEqual("manual_001", manual["id"])
        self.assertEqual("manual", manual["taxonomy"])
        self.assertEqual("manual", manual["review_origin"])
        self.assertEqual({"x": 70, "y": 70, "width": 10, "height": 10}, manual["bbox_source"])
        self.assertEqual(1, reviewed["review_summary"]["manual_kept_count"])

    def test_manual_asset_id_conflict_with_original_fails(self):
        overrides = make_overrides_document()
        overrides["manual_assets"] = [
            {
                "asset_ref": "asset_001",
                "bbox": {"x": 70, "y": 70, "width": 10, "height": 10},
            }
        ]
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("conflicts with original asset id", str(context.exception))

    def test_duplicate_manual_asset_id_fails(self):
        overrides = make_overrides_document()
        overrides["manual_assets"] = [
            {
                "asset_ref": "manual_001",
                "bbox": {"x": 70, "y": 70, "width": 10, "height": 10},
            },
            {
                "asset_ref": "manual_001",
                "bbox": {"x": 80, "y": 80, "width": 5, "height": 5},
            },
        ]
        with self.assertRaises(ValueError) as context:
            run_apply(overrides=overrides)
        self.assertIn("duplicate manual asset id", str(context.exception))

    def test_manual_drop_is_recorded_not_emitted(self):
        overrides = make_overrides_document()
        overrides["manual_assets"] = [
            {
                "asset_ref": "manual_001",
                "bbox": {"x": 70, "y": 70, "width": 10, "height": 10},
                "decision": "DROP",
            }
        ]
        reviewed = run_apply(overrides=overrides)
        self.assertEqual(3, reviewed["review_summary"]["final_asset_count"])
        self.assertEqual(1, reviewed["review_summary"]["manual_dropped_count"])


class ReviewedOutputSchemaTests(unittest.TestCase):
    def test_reviewed_document_schema_and_provenance(self):
        reviewed = run_apply()
        self.assertEqual(
            apply_review.REVIEWED_SCHEMA_VERSION, reviewed["schema_version"]
        )
        self.assertEqual("0.1", reviewed["source_direct_assets_schema_version"])
        self.assertEqual("direct-assets.json", reviewed["source_assets_json"])
        self.assertEqual("review-overrides.json", reviewed["review_overrides_json"])
        self.assertEqual("source.png", reviewed["source_image"])
        self.assertEqual({"width": 100, "height": 100}, reviewed["source_image_size"])

    def test_review_summary_counts(self):
        overrides = make_overrides_document()
        overrides["overrides"] = {"asset_001": {"decision": "DROP"}}
        overrides["manual_assets"] = [
            {
                "asset_ref": "manual_001",
                "bbox": {"x": 70, "y": 70, "width": 10, "height": 10},
            }
        ]
        reviewed = run_apply(overrides=overrides)
        summary = reviewed["review_summary"]
        self.assertEqual(3, summary["original_asset_count"])
        self.assertEqual(1, summary["dropped_count"])
        self.assertEqual(1, summary["manual_kept_count"])
        self.assertEqual(3, summary["final_asset_count"])

    def test_unchanged_assets_remain_byte_identical(self):
        document = make_assets_document()
        overrides = make_overrides_document()
        overrides["overrides"] = {"asset_001": {"decision": "DROP"}}
        reviewed = run_apply(document, overrides)
        self.assertEqual(document["assets"][1], reviewed["assets"][0])
        self.assertEqual(document["assets"][2], reviewed["assets"][1])

    def test_inputs_are_not_mutated(self):
        document = make_assets_document()
        overrides = make_overrides_document()
        overrides["overrides"] = {"asset_001": {"decision": "DROP"}}
        document_copy = copy.deepcopy(document)
        overrides_copy = copy.deepcopy(overrides)
        run_apply(document, overrides)
        self.assertEqual(document_copy, document)
        self.assertEqual(overrides_copy, overrides)

    def test_cli_writes_output_atomically(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            assets_path = directory / "direct-assets.json"
            overrides_path = directory / "review-overrides.json"
            output_path = directory / "reviewed-direct-assets.json"
            assets_path.write_text(
                json.dumps(make_assets_document()), encoding="utf-8"
            )
            overrides_path.write_text(
                json.dumps(make_overrides_document()), encoding="utf-8"
            )
            code = apply_review.main(
                [
                    "--assets-json",
                    str(assets_path),
                    "--overrides-json",
                    str(overrides_path),
                    "--output-json",
                    str(output_path),
                ]
            )
            self.assertEqual(0, code)
            reviewed = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                apply_review.REVIEWED_SCHEMA_VERSION, reviewed["schema_version"]
            )

    def test_cli_malformed_overrides_fail_fast(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            assets_path = directory / "direct-assets.json"
            overrides_path = directory / "review-overrides.json"
            output_path = directory / "reviewed-direct-assets.json"
            assets_path.write_text(
                json.dumps(make_assets_document()), encoding="utf-8"
            )
            overrides_path.write_text("not-json{", encoding="utf-8")
            with self.assertRaises(SystemExit) as context:
                apply_review.main(
                    [
                        "--assets-json",
                        str(assets_path),
                        "--overrides-json",
                        str(overrides_path),
                        "--output-json",
                        str(output_path),
                    ]
                )
            self.assertIn("invalid JSON", str(context.exception))
            self.assertFalse(output_path.exists())

    def test_cli_overrides_referencing_unknown_asset_fail_fast(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            assets_path = directory / "direct-assets.json"
            overrides_path = directory / "review-overrides.json"
            output_path = directory / "reviewed-direct-assets.json"
            assets_path.write_text(
                json.dumps(make_assets_document()), encoding="utf-8"
            )
            overrides = make_overrides_document()
            overrides["overrides"] = {"asset_999": {"decision": "DROP"}}
            overrides_path.write_text(json.dumps(overrides), encoding="utf-8")
            with self.assertRaises(SystemExit) as context:
                apply_review.main(
                    [
                        "--assets-json",
                        str(assets_path),
                        "--overrides-json",
                        str(overrides_path),
                        "--output-json",
                        str(output_path),
                    ]
                )
            self.assertIn("unknown asset id(s)", str(context.exception))
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
