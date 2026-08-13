from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bbox_refiner as refiner  # noqa: E402


BACKGROUND = (14, 22, 38)
FOREGROUND = (235, 205, 70)


def icon_asset(
    asset_id: str,
    bbox: dict[str, int],
    *,
    semantic_type: str = "icon",
    strategy: str = "direct_crop",
    should_extract: bool = True,
) -> dict:
    return {
        "id": asset_id,
        "label": asset_id,
        "semantic_type": semantic_type,
        "bbox": bbox,
        "should_extract": should_extract,
        "strategy": strategy,
        "issues": [] if strategy != "advanced_required" else ["complex_background"],
        "reason": "Synthetic fixture.",
    }


def analysis_for(image_path: Path, size: tuple[int, int], assets: list[dict]) -> dict:
    return {
        "schema_version": "0.1",
        "source_image": image_path.name,
        "source_size": {"width": size[0], "height": size[1]},
        "taxonomy_version": "game-ui-asset-taxonomy-v0.1",
        "assets": assets,
    }


def bbox_edge_error(actual: dict[str, int], expected: dict[str, int]) -> int:
    actual_edges = refiner.bbox_edges(actual)
    expected_edges = refiner.bbox_edges(expected)
    return sum(abs(a - b) for a, b in zip(actual_edges, expected_edges))


class BBoxRefinerTests(unittest.TestCase):
    def test_refinement_schema_is_valid_draft_2020_12(self):
        schema = refiner.load_json(refiner.REFINEMENT_SCHEMA_PATH)
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        Draft202012Validator.check_schema(schema)

    def test_roi_expansion_uses_default_and_clamps_bounds(self):
        self.assertEqual(
            {"x": 6, "y": 6, "width": 58, "height": 58},
            refiner.expand_bbox(
                {"x": 20, "y": 20, "width": 30, "height": 30},
                (100, 80),
            ),
        )
        self.assertEqual(
            {"x": 2, "y": 2, "width": 46, "height": 46},
            refiner.expand_bbox(
                {"x": 10, "y": 10, "width": 30, "height": 30},
                (100, 80),
                expand_px=8,
            ),
        )
        self.assertEqual(
            {"x": 0, "y": 0, "width": 23, "height": 23},
            refiner.expand_bbox(
                {"x": 1, "y": 1, "width": 14, "height": 14},
                (100, 80),
                expand_px=8,
            ),
        )
        self.assertEqual(
            {"x": 77, "y": 57, "width": 23, "height": 23},
            refiner.expand_bbox(
                {"x": 85, "y": 65, "width": 14, "height": 14},
                (100, 80),
                expand_px=8,
            ),
        )

    def test_synthetic_icon_refinement_improves_coarse_bbox(self):
        image = np.full((80, 100, 3), BACKGROUND, dtype=np.uint8)
        ground_truth = {"x": 40, "y": 30, "width": 20, "height": 20}
        image[30:50, 40:60] = FOREGROUND
        coarse = {"x": 35, "y": 27, "width": 28, "height": 28}

        result, _mask = refiner.refine_icon(
            image,
            icon_asset("icon_001", coarse),
            safety_padding=1,
        )

        self.assertEqual("success", result["status"])
        self.assertEqual("refined", result["use_bbox"])
        self.assertLess(
            bbox_edge_error(result["refined_bbox"], ground_truth),
            bbox_edge_error(coarse, ground_truth),
        )
        self.assertEqual({"x": 39, "y": 29, "width": 22, "height": 22}, result["refined_bbox"])
        self.assertGreater(result["confidence"], 0.5)

    def test_multiple_components_are_merged_into_one_icon_bbox(self):
        image = np.full((80, 100, 3), BACKGROUND, dtype=np.uint8)
        image[30:45, 40:48] = (70, 190, 255)
        image[32:47, 52:60] = (80, 210, 255)
        image[39:48, 48:52] = (90, 225, 255)
        coarse = {"x": 36, "y": 27, "width": 28, "height": 25}

        result, _mask = refiner.refine_icon(
            image,
            icon_asset("icon_001", coarse),
            safety_padding=1,
        )

        self.assertEqual("success", result["status"])
        refined = result["refined_bbox"]
        self.assertLessEqual(refined["x"], 40)
        self.assertGreaterEqual(refined["x"] + refined["width"], 60)
        self.assertLessEqual(refined["y"], 30)
        self.assertGreaterEqual(refined["y"] + refined["height"], 48)

    def test_far_small_noise_is_rejected(self):
        image = np.full((90, 110, 3), BACKGROUND, dtype=np.uint8)
        image[35:55, 45:65] = FOREGROUND
        image[18:20, 22:24] = (255, 0, 255)
        image[70:72, 87:89] = (255, 0, 255)
        coarse = {"x": 40, "y": 31, "width": 28, "height": 28}

        result, _mask = refiner.refine_icon(
            image,
            icon_asset("icon_001", coarse),
            expand_px=25,
            safety_padding=1,
        )

        self.assertEqual("success", result["status"])
        self.assertEqual("refined", result["use_bbox"])
        refined = result["refined_bbox"]
        self.assertGreater(refined["x"], 22)
        self.assertLess(refined["x"] + refined["width"], 87)
        self.assertEqual({"x": 44, "y": 34, "width": 22, "height": 22}, refined)

    def test_uniform_roi_fails_instead_of_inventing_bbox(self):
        image = np.full((80, 100, 3), BACKGROUND, dtype=np.uint8)
        coarse = {"x": 35, "y": 27, "width": 28, "height": 28}

        result, mask = refiner.refine_icon(image, icon_asset("icon_001", coarse))

        self.assertEqual("failed", result["status"])
        self.assertEqual("coarse", result["use_bbox"])
        self.assertIsNone(result["refined_bbox"])
        self.assertEqual(0.0, result["confidence"])
        self.assertIn("no relevant foreground", result["failure_reason"])
        self.assertFalse(mask.any())

    def test_icons_at_source_bounds_remain_in_bounds(self):
        for name, target, coarse in (
            (
                "top_left",
                {"x": 0, "y": 0, "width": 12, "height": 12},
                {"x": 0, "y": 0, "width": 17, "height": 17},
            ),
            (
                "bottom_right",
                {"x": 88, "y": 68, "width": 12, "height": 12},
                {"x": 84, "y": 64, "width": 16, "height": 16},
            ),
        ):
            with self.subTest(name=name):
                image = np.full((80, 100, 3), BACKGROUND, dtype=np.uint8)
                x1, y1, x2, y2 = refiner.bbox_edges(target)
                image[y1:y2, x1:x2] = FOREGROUND
                result, _mask = refiner.refine_icon(
                    image,
                    icon_asset("icon_001", coarse),
                    safety_padding=2,
                )
                self.assertEqual("success", result["status"])
                for bbox in (result["roi_bbox"], result["refined_bbox"]):
                    self.assertGreaterEqual(bbox["x"], 0)
                    self.assertGreaterEqual(bbox["y"], 0)
                    self.assertLessEqual(bbox["x"] + bbox["width"], 100)
                    self.assertLessEqual(bbox["y"] + bbox["height"], 80)

    def test_unsupported_assets_are_skipped(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            image_path = temp / "preview.png"
            Image.new("RGB", (100, 80), BACKGROUND).save(image_path)
            assets = [
                icon_asset(
                    "panel_001",
                    {"x": 5, "y": 5, "width": 30, "height": 20},
                    semantic_type="panel",
                ),
                icon_asset(
                    "illustration_001",
                    {"x": 40, "y": 5, "width": 30, "height": 30},
                    semantic_type="illustration",
                ),
                icon_asset(
                    "icon_001",
                    {"x": 5, "y": 45, "width": 20, "height": 20},
                    strategy="advanced_required",
                ),
            ]
            document = refiner.refine_document(
                image_path,
                analysis_for(image_path, (100, 80), assets),
            )

        self.assertEqual(["skipped", "skipped", "skipped"], [r["status"] for r in document["refinements"]])
        self.assertEqual(["coarse", "coarse", "coarse"], [r["use_bbox"] for r in document["refinements"]])
        self.assertEqual([], refiner.validate_refinement(document))

    def test_ids_filter_processes_only_synthetic_requested_asset(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            image_path = temp / "preview.png"
            image = Image.new("RGB", (100, 80), BACKGROUND)
            pixels = np.asarray(image).copy()
            pixels[30:50, 40:60] = FOREGROUND
            Image.fromarray(pixels).save(image_path)
            assets = [
                icon_asset("icon_001", {"x": 35, "y": 27, "width": 28, "height": 28}),
                icon_asset("icon_002", {"x": 5, "y": 58, "width": 20, "height": 20}),
            ]
            document = refiner.refine_document(
                image_path,
                analysis_for(image_path, (100, 80), assets),
                ids={"icon_001"},
            )

        self.assertEqual(["icon_001"], [r["asset_id"] for r in document["refinements"]])
        self.assertEqual("success", document["refinements"][0]["status"])
        self.assertEqual("refined", document["refinements"][0]["use_bbox"])

    def test_acceptance_gate_rejects_area_expansion_over_two_times(self):
        coarse = {"x": 40, "y": 30, "width": 20, "height": 20}
        refined = {"x": 35, "y": 25, "width": 30, "height": 30}
        metrics = {
            "center_shift_px": 0.0,
            "area_ratio": 2.25,
            "foreground_pixel_ratio": 0.8,
        }
        result = refiner._finalize_icon_result(
            {"asset_id": "icon_001", "coarse_bbox": coarse, "roi_bbox": coarse},
            coarse,
            refined,
            metrics,
        )
        self.assertEqual("fallback", result["status"])
        self.assertEqual("coarse", result["use_bbox"])
        self.assertEqual("refined bbox rejected by acceptance gate", result["failure_reason"])

    def test_acceptance_gate_rejects_center_shift_over_ten_pixels(self):
        coarse = {"x": 40, "y": 30, "width": 20, "height": 20}
        refined = {"x": 51, "y": 30, "width": 20, "height": 20}
        metrics = {
            "center_shift_px": 11.0,
            "area_ratio": 1.0,
            "foreground_pixel_ratio": 0.8,
        }
        result = refiner._finalize_icon_result(
            {"asset_id": "icon_001", "coarse_bbox": coarse, "roi_bbox": coarse},
            coarse,
            refined,
            metrics,
        )
        self.assertEqual("fallback", result["status"])
        self.assertEqual("coarse", result["use_bbox"])

    def test_cli_writes_debug_artifacts_without_modifying_analysis(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            image_path = temp / "preview.png"
            analysis_path = temp / "asset-analysis.json"
            output_path = temp / "bbox-refinement.json"
            debug_dir = temp / "debug-refiner"
            pixels = np.full((80, 100, 3), BACKGROUND, dtype=np.uint8)
            pixels[30:50, 40:60] = FOREGROUND
            Image.fromarray(pixels).save(image_path)
            analysis = analysis_for(
                image_path,
                (100, 80),
                [icon_asset("icon_001", {"x": 35, "y": 27, "width": 28, "height": 28})],
            )
            original_text = json.dumps(analysis, indent=2) + "\n"
            analysis_path.write_text(original_text, encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "bbox_refiner.py"),
                    "--source-image",
                    str(image_path),
                    "--asset-analysis",
                    str(analysis_path),
                    "--output",
                    str(output_path),
                    "--debug-dir",
                    str(debug_dir),
                    "--ids",
                    "icon_001",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(original_text, analysis_path.read_text(encoding="utf-8"))
            document = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual([], refiner.validate_refinement(document))
            self.assertTrue((debug_dir / "icon_001-roi.png").is_file())
            self.assertTrue((debug_dir / "icon_001-mask.png").is_file())
            self.assertTrue((debug_dir / "icon_001-overlay.png").is_file())


if __name__ == "__main__":
    unittest.main()
