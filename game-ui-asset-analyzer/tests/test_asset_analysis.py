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
EXAMPLES = ROOT / "examples"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_asset_analysis as builder  # noqa: E402
import prepare_analysis_input as preparer  # noqa: E402
import validate_asset_analysis as validator  # noqa: E402


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def bboxes_overlap(left: dict[str, int], right: dict[str, int]) -> bool:
    return (
        left["x"] < right["x"] + right["width"]
        and right["x"] < left["x"] + left["width"]
        and left["y"] < right["y"] + right["height"]
        and right["y"] < left["y"] + left["height"]
    )


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
                self.assertEqual(
                    {
                        "direct_crop",
                        "foreground_extract",
                        "advanced_required",
                        "do_not_extract",
                    },
                    set(schema["$defs"]["strategy"]["enum"]),
                )

    def test_prepare_1248_by_832_to_1024_by_683(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            analysis_image = temp / "analysis-input.png"
            metadata_path = temp / "analysis-input-meta.json"
            Image.new("RGB", (1248, 832), "white").save(source)

            metadata = preparer.prepare_analysis_input(
                source,
                analysis_image,
                metadata_path,
            )
            with Image.open(analysis_image) as image:
                self.assertEqual("PNG", image.format)
                self.assertEqual((1024, 683), image.size)

        self.assertEqual(
            {"width": 1024, "height": 683},
            metadata["analysis_size"],
        )

    def test_prepare_does_not_upscale_small_image(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            analysis_image = temp / "analysis-input.png"
            metadata_path = temp / "analysis-input-meta.json"
            Image.new("RGBA", (800, 600), (0, 0, 0, 0)).save(source)

            metadata = preparer.prepare_analysis_input(
                source,
                analysis_image,
                metadata_path,
            )
            with Image.open(analysis_image) as image:
                self.assertEqual((800, 600), image.size)

        self.assertEqual(
            {"width": 800, "height": 600},
            metadata["analysis_size"],
        )

    def test_prepare_metadata_uses_actual_image_dimensions(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            analysis_image = temp / "analysis-input.png"
            metadata_path = temp / "analysis-input-meta.json"
            Image.new("RGB", (1248, 832), "white").save(source)

            preparer.prepare_analysis_input(source, analysis_image, metadata_path)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual("preview.png", metadata["source_image"])
        self.assertEqual({"width": 1248, "height": 832}, metadata["source_size"])
        self.assertEqual("analysis-input.png", metadata["analysis_image"])
        self.assertEqual({"width": 1024, "height": 683}, metadata["analysis_size"])
        self.assertEqual(1248 / 1024, metadata["scale_to_source"]["x"])
        self.assertEqual(832 / 683, metadata["scale_to_source"]["y"])

    def test_bbox_mapping_uses_actual_per_axis_scales(self):
        mapped = builder.map_bbox_to_source(
            {"x": 716, "y": 311, "width": 28, "height": 28},
            analysis_size=(1024, 683),
            source_size=(1248, 832),
        )
        self.assertEqual(
            {"x": 873, "y": 379, "width": 34, "height": 34},
            mapped,
        )

    def test_bbox_mapping_clamps_right_bottom_boundary(self):
        mapped = builder.map_bbox_to_source(
            {"x": 1000, "y": 660, "width": 24, "height": 23},
            analysis_size=(1024, 683),
            source_size=(1248, 832),
        )
        self.assertGreater(mapped["width"], 0)
        self.assertGreater(mapped["height"], 0)
        self.assertLessEqual(mapped["x"] + mapped["width"], 1248)
        self.assertLessEqual(mapped["y"] + mapped["height"], 832)
        self.assertEqual(1248, mapped["x"] + mapped["width"])
        self.assertEqual(832, mapped["y"] + mapped["height"])

    def test_builder_maps_bbox_from_analysis_image_to_source(self):
        candidates = [
            {
                "label": "Mapped icon",
                "semantic_type": "icon",
                "bbox": {"x": 716, "y": 311, "width": 28, "height": 28},
                "should_extract": True,
                "strategy": "direct_crop",
                "issues": [],
                "reason": "Clean analysis-image bounds.",
            }
        ]
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            analysis_image = temp / "analysis-input.png"
            Image.new("RGB", (1248, 832), "white").save(source)
            Image.new("RGB", (1024, 683), "white").save(analysis_image)
            analysis = builder.build_analysis(source, candidates, analysis_image)

        self.assertEqual(
            {"x": 873, "y": 379, "width": 34, "height": 34},
            analysis["assets"][0]["bbox"],
        )

    def test_builder_without_analysis_image_preserves_bbox(self):
        candidates = load_fixture("valid-candidates.json")
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "preview.png"
            Image.new("RGB", (100, 80), "white").save(source)
            analysis = builder.build_analysis(source, candidates)

        by_label = {asset["label"]: asset for asset in analysis["assets"]}
        self.assertEqual(
            {"x": 5, "y": 5, "width": 16, "height": 16},
            by_label["Currency icon"]["bbox"],
        )

    def test_builder_validates_candidates_against_analysis_image(self):
        candidates = [
            {
                "label": "Outside analysis image",
                "semantic_type": "icon",
                "bbox": {"x": 1020, "y": 10, "width": 20, "height": 20},
                "should_extract": True,
                "strategy": "direct_crop",
                "issues": [],
                "reason": "Valid in source width but invalid in analysis width.",
            }
        ]
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            analysis_image = temp / "analysis-input.png"
            Image.new("RGB", (1248, 832), "white").save(source)
            Image.new("RGB", (1024, 683), "white").save(analysis_image)
            with self.assertRaisesRegex(ValueError, "analysis image width"):
                builder.build_analysis(source, candidates, analysis_image)

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

    def test_foreground_extract_with_issue_is_valid(self):
        candidate = copy.deepcopy(load_fixture("valid-candidates.json")[1])
        candidate["strategy"] = "foreground_extract"
        candidate["issues"] = ["complex_background"]
        self.assertEqual([], validator.validate_candidates([candidate], (100, 80)))

    def test_foreground_extract_without_issue_is_valid(self):
        candidate = copy.deepcopy(load_fixture("valid-candidates.json")[1])
        candidate["strategy"] = "foreground_extract"
        candidate["issues"] = []
        self.assertEqual([], validator.validate_candidates([candidate], (100, 80)))

    def test_foreground_extract_with_should_extract_false_fails(self):
        candidates = load_fixture("invalid-foreground-strategy-mismatch.json")
        errors = validator.validate_candidates(candidates, (100, 80))
        self.assert_has_error(errors, "should_extract")

    def test_compound_card_fixture_keeps_overlapping_parent_and_children(self):
        candidates = load_fixture("compound-card-candidates.json")
        self.assertEqual([], validator.validate_candidates(candidates, (800, 600)))

        by_label = {candidate["label"]: candidate for candidate in candidates}
        expected_types = {
            "Offer card surface": "panel",
            "Crystal bundle illustration": "illustration",
            "Purchase price button": "button",
            "Bundle amount text": "text",
            "Bonus amount text": "text",
            "BEST VALUE decoration": "decoration",
        }
        self.assertEqual(
            expected_types,
            {label: by_label[label]["semantic_type"] for label in expected_types},
        )
        self.assertEqual(
            {
                "direct_crop",
                "foreground_extract",
                "advanced_required",
                "do_not_extract",
            },
            {candidate["strategy"] for candidate in candidates},
        )
        self.assertEqual(
            "foreground_extract",
            by_label["Crystal bundle illustration"]["strategy"],
        )

        parent_bbox = by_label["Offer card surface"]["bbox"]
        for child_label in expected_types.keys() - {"Offer card surface"}:
            with self.subTest(child=child_label):
                self.assertTrue(bboxes_overlap(parent_bbox, by_label[child_label]["bbox"]))
                self.assertIn("should_extract", by_label[child_label])
                self.assertIn("strategy", by_label[child_label])

    def test_public_compound_card_example_matches_validated_fixture(self):
        example = json.loads(
            (EXAMPLES / "asset-candidates.json").read_text(encoding="utf-8")
        )
        self.assertEqual(load_fixture("compound-card-candidates.json"), example)
        self.assertEqual([], validator.validate_candidates(example, (800, 600)))

    def test_bottom_help_bar_fixture_keeps_panel_and_internal_icons(self):
        candidates = load_fixture("bottom-help-bar-candidates.json")
        self.assertEqual([], validator.validate_candidates(candidates, (800, 600)))

        panel = next(
            candidate
            for candidate in candidates
            if candidate["label"] == "Bottom help panel"
        )
        icons = [
            candidate for candidate in candidates if candidate["semantic_type"] == "icon"
        ]
        self.assertEqual(
            {"Secure payment icon", "Instant credit icon", "Help icon"},
            {icon["label"] for icon in icons},
        )
        self.assertTrue(all(bboxes_overlap(panel["bbox"], icon["bbox"]) for icon in icons))

    def test_builder_maps_compound_fixture_without_losing_candidates(self):
        candidates = load_fixture("compound-card-candidates.json")
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            analysis_image = temp / "analysis-input.png"
            Image.new("RGB", (1600, 1200), "white").save(source)
            Image.new("RGB", (800, 600), "white").save(analysis_image)
            analysis = builder.build_analysis(source, candidates, analysis_image)

        self.assertEqual(len(candidates), len(analysis["assets"]))
        by_label = {asset["label"]: asset for asset in analysis["assets"]}
        self.assertEqual(
            {"x": 200, "y": 160, "width": 600, "height": 760},
            by_label["Offer card surface"]["bbox"],
        )
        self.assertEqual(
            {"x": 290, "y": 270, "width": 420, "height": 300},
            by_label["Crystal bundle illustration"]["bbox"],
        )
        self.assertEqual(
            "foreground_extract",
            by_label["Crystal bundle illustration"]["strategy"],
        )

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

    def test_preparer_and_mapped_builder_cli_succeed(self):
        candidates = [
            {
                "label": "Mapped icon",
                "semantic_type": "icon",
                "bbox": {"x": 716, "y": 311, "width": 28, "height": 28},
                "should_extract": True,
                "strategy": "direct_crop",
                "issues": [],
                "reason": "Clean analysis-image bounds.",
            }
        ]
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            analysis_image = temp / "analysis-input.png"
            metadata_path = temp / "analysis-input-meta.json"
            candidates_path = temp / "asset-candidates.json"
            output_path = temp / "asset-analysis.json"
            Image.new("RGB", (1248, 832), "white").save(source)
            candidates_path.write_text(json.dumps(candidates), encoding="utf-8")

            prepare_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "prepare_analysis_input.py"),
                    "--source-image",
                    str(source),
                    "--output-image",
                    str(analysis_image),
                    "--metadata-output",
                    str(metadata_path),
                    "--max-width",
                    "1024",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, prepare_result.returncode, prepare_result.stderr)
            self.assertIn("1248x832 -> 1024x683", prepare_result.stdout)

            build_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_asset_analysis.py"),
                    "--source-image",
                    str(source),
                    "--analysis-image",
                    str(analysis_image),
                    "--model-output",
                    str(candidates_path),
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
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(0, validate_result.returncode, validate_result.stderr)
        self.assertEqual(
            {"x": 873, "y": 379, "width": 34, "height": 34},
            result["assets"][0]["bbox"],
        )

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
