from __future__ import annotations

import copy
import hashlib
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
REPO_ROOT = ROOT.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import extract_assets as extractor  # noqa: E402


BACKGROUND = (14, 22, 38, 255)
FOREGROUND = (235, 205, 70, 255)


def request_for(
    source: Path,
    bbox: dict[str, int],
    *,
    mode: str = "foreground_extract",
    asset_id: str = "icon_001",
    asset_type: str = "icon",
    config: dict | None = None,
) -> dict:
    document = {
        "schema_version": "0.1",
        "source_image": str(source),
        "assets": [
            {
                "asset_id": asset_id,
                "asset_type": asset_type,
                "final_bbox": bbox,
                "extraction_mode": mode,
            }
        ],
    }
    if config is not None:
        document["config"] = config
    return document


def save_refiner_style_icon(path: Path, *, alpha: int = 255) -> dict[str, int]:
    pixels = np.full((80, 100, 4), BACKGROUND, dtype=np.uint8)
    pixels[30:50, 40:60] = (*FOREGROUND[:3], alpha)
    Image.fromarray(pixels, "RGBA").save(path)
    return {"x": 36, "y": 26, "width": 28, "height": 28}


class SchemaTests(unittest.TestCase):
    def test_schemas_are_valid_draft_2020_12(self):
        for schema_path in (
            extractor.REQUEST_SCHEMA_PATH,
            extractor.RESULT_SCHEMA_PATH,
        ):
            with self.subTest(schema=schema_path.name):
                schema = extractor.load_json(schema_path)
                self.assertEqual(
                    "https://json-schema.org/draft/2020-12/schema",
                    schema["$schema"],
                )
                Draft202012Validator.check_schema(schema)

    def test_duplicate_asset_ids_are_rejected(self):
        request = request_for(Path("preview.png"), {"x": 0, "y": 0, "width": 1, "height": 1})
        request["assets"].append(copy.deepcopy(request["assets"][0]))
        self.assertTrue(any("duplicate asset_id" in error for error in extractor.validate_request(request)))


class RoiAndBackgroundTests(unittest.TestCase):
    def test_roi_clamps_at_each_source_edge_without_changing_final_bbox(self):
        cases = {
            "top": (
                {"x": 40, "y": 0, "width": 20, "height": 20},
                {"x": 30, "y": 0, "width": 40, "height": 30},
                {"x": 10, "y": 0},
            ),
            "left": (
                {"x": 0, "y": 30, "width": 20, "height": 20},
                {"x": 0, "y": 20, "width": 30, "height": 40},
                {"x": 0, "y": 10},
            ),
            "right": (
                {"x": 80, "y": 30, "width": 20, "height": 20},
                {"x": 70, "y": 20, "width": 30, "height": 40},
                {"x": 10, "y": 10},
            ),
            "bottom": (
                {"x": 40, "y": 60, "width": 20, "height": 20},
                {"x": 30, "y": 50, "width": 40, "height": 30},
                {"x": 10, "y": 10},
            ),
        }
        for name, (bbox, expected_roi, expected_offset) in cases.items():
            with self.subTest(name=name):
                original = copy.deepcopy(bbox)
                roi, offset = extractor.build_extraction_roi(bbox, (100, 80), 10)
                self.assertEqual(expected_roi, roi)
                self.assertEqual(expected_offset, offset)
                self.assertEqual(original, bbox)

    def test_context_ring_excludes_final_bbox_core(self):
        bbox = {"x": 20, "y": 20, "width": 10, "height": 8}
        roi, offset = extractor.build_extraction_roi(bbox, (50, 50), 5)
        ring = extractor.context_ring_mask((roi["height"], roi["width"]), bbox, offset)
        self.assertEqual(10 * 8, int((~ring).sum()))
        self.assertEqual(roi["width"] * roi["height"] - 80, int(ring.sum()))


class ExtractionTests(unittest.TestCase):
    def test_foreground_extract_writes_roi_mask_rgba_and_traceable_metadata(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            final_bbox = save_refiner_style_icon(source)
            request = request_for(source, final_bbox)
            original_request = copy.deepcopy(request)

            result = extractor.execute_request(request, temp / "output")
            item = result["assets"][0]
            asset_path = temp / "output" / item["output_path"]
            mask_path = temp / "output" / item["mask_path"]

            self.assertEqual("success", result["status"])
            self.assertEqual("success", item["status"])
            self.assertEqual("context_ring_median", item["background_method"])
            self.assertEqual(list(BACKGROUND[:3]), item["background_rgb"])
            self.assertEqual(22.0, item["mask_threshold"])
            self.assertEqual(final_bbox, item["final_bbox"])
            self.assertEqual(original_request, request)
            self.assertEqual({"x": 26, "y": 16, "width": 48, "height": 48}, item["extraction_roi"])
            self.assertEqual({"x": 10, "y": 10}, item["final_bbox_offset"])
            self.assertTrue(asset_path.is_file())
            self.assertTrue(mask_path.is_file())

            with Image.open(asset_path) as asset_image:
                self.assertEqual("RGBA", asset_image.mode)
                self.assertEqual((48, 48), asset_image.size)
                alpha = np.asarray(asset_image)[:, :, 3]
                self.assertEqual(0, int(alpha[0, 0]))
                self.assertGreater(int(alpha[24, 24]), 0)
            with Image.open(mask_path) as mask_image:
                values = set(np.unique(np.asarray(mask_image)).tolist())
                self.assertEqual("L", mask_image.mode)
                self.assertTrue(values.issubset({0, 255}))
                self.assertIn(0, values)
                self.assertIn(255, values)

            saved = json.loads((temp / "output" / "extraction-result.json").read_text(encoding="utf-8"))
            self.assertEqual(result, saved)
            self.assertEqual([], extractor.validate_result(saved))

    def test_direct_crop_uses_existing_real_ui_asset_and_preserves_pixels(self):
        source = REPO_ROOT / "game-ui-auto-composer-skill" / "assets" / "login" / "login_button.jpg"
        self.assertTrue(source.is_file(), "expected committed real UI fixture")
        bbox = {"x": 25, "y": 20, "width": 160, "height": 80}
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "output"
            result = extractor.execute_request(
                request_for(source, bbox, mode="direct_crop", asset_type="button", asset_id="button_001"),
                output,
            )
            item = result["assets"][0]
            with Image.open(source) as image:
                expected = np.asarray(image.convert("RGBA"))[20:100, 25:185]
            with Image.open(output / item["output_path"]) as image:
                actual = np.asarray(image)

            self.assertTrue(np.array_equal(expected, actual))
            self.assertEqual(bbox, item["extraction_roi"])
            self.assertEqual(0, item["roi_padding"])
            self.assertIsNone(item["mask_path"])
            self.assertIsNone(item["background_method"])
            self.assertFalse((output / "masks").exists())

    def test_source_alpha_is_multiplied_not_overwritten(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = save_refiner_style_icon(source, alpha=128)
            result = extractor.execute_request(request_for(source, bbox), temp / "output")
            item = result["assets"][0]
            with Image.open(temp / "output" / item["output_path"]) as image:
                alpha = np.asarray(image)[:, :, 3]
            self.assertEqual(128, int(alpha[24, 24]))
            self.assertEqual("multiply", item["alpha_parameters"]["source_alpha_rule"])
            self.assertEqual("straight", item["alpha_parameters"]["alpha_representation"])

    def test_uniform_core_reports_failed_empty_mask(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            Image.new("RGBA", (100, 80), BACKGROUND).save(source)
            request = request_for(source, {"x": 35, "y": 27, "width": 28, "height": 28})
            result = extractor.execute_request(request, temp / "output")
            item = result["assets"][0]
            self.assertEqual("failed", result["status"])
            self.assertEqual("failed", item["status"])
            self.assertIn("mask is empty", item["failure_reason"])
            self.assertEqual("context_ring_median", item["background_method"])
            self.assertIsNotNone(item["extraction_roi"])
            self.assertIsNone(item["output_path"])
            self.assertFalse((temp / "output" / "assets" / "icon_001.png").exists())

    def test_missing_source_image_records_program_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            missing = temp / "missing.png"
            result = extractor.execute_request(
                request_for(missing, {"x": 1, "y": 1, "width": 10, "height": 10}),
                temp / "output",
            )
            item = result["assets"][0]
            self.assertEqual("failed", result["status"])
            self.assertIn("not found", item["failure_reason"])
            self.assertIsNone(result["source_size"])

    def test_context_ring_shortage_uses_recorded_roi_border_fallback(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = save_refiner_style_icon(source)
            result = extractor.execute_request(
                request_for(source, bbox, config={"roi_padding": 0}),
                temp / "output",
            )
            item = result["assets"][0]
            self.assertEqual("success", item["status"])
            self.assertEqual("roi_border_median_fallback", item["background_method"])
            self.assertEqual(0, item["background_parameters"]["context_ring_pixel_count"])

    def test_foreground_extraction_succeeds_at_all_source_edges(self):
        cases = {
            "top": {"x": 36, "y": 0, "width": 28, "height": 28},
            "left": {"x": 0, "y": 26, "width": 28, "height": 28},
            "right": {"x": 72, "y": 26, "width": 28, "height": 28},
            "bottom": {"x": 36, "y": 52, "width": 28, "height": 28},
        }
        for name, bbox in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                temp = Path(raw)
                source = temp / "preview.png"
                pixels = np.full((80, 100, 4), BACKGROUND, dtype=np.uint8)
                x1 = bbox["x"] + 4
                y1 = bbox["y"] + 4
                pixels[y1:y1 + 20, x1:x1 + 20] = FOREGROUND
                Image.fromarray(pixels, "RGBA").save(source)
                result = extractor.execute_request(request_for(source, bbox), temp / "output")
                item = result["assets"][0]
                roi = item["extraction_roi"]
                self.assertEqual("success", item["status"])
                self.assertGreaterEqual(roi["x"], 0)
                self.assertGreaterEqual(roi["y"], 0)
                self.assertLessEqual(roi["x"] + roi["width"], 100)
                self.assertLessEqual(roi["y"] + roi["height"], 80)

    def test_out_of_bounds_final_bbox_fails_without_clamping_it(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            Image.new("RGBA", (100, 80), BACKGROUND).save(source)
            bbox = {"x": 90, "y": 70, "width": 20, "height": 20}
            result = extractor.execute_request(request_for(source, bbox), temp / "output")
            item = result["assets"][0]
            self.assertEqual("failed", item["status"])
            self.assertEqual(bbox, item["final_bbox"])
            self.assertIsNone(item["extraction_roi"])

    def test_same_input_and_config_produce_identical_png_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = save_refiner_style_icon(source)
            request = request_for(source, bbox)
            hashes = []
            for name in ("first", "second"):
                output = temp / name
                result = extractor.execute_request(request, output)
                asset_path = output / result["assets"][0]["output_path"]
                mask_path = output / result["assets"][0]["mask_path"]
                hashes.append(
                    (
                        hashlib.sha256(asset_path.read_bytes()).hexdigest(),
                        hashlib.sha256(mask_path.read_bytes()).hexdigest(),
                    )
                )
            self.assertEqual(hashes[0], hashes[1])


class CliTests(unittest.TestCase):
    def test_cli_resolves_source_relative_to_request_file(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            source = temp / "preview.png"
            bbox = save_refiner_style_icon(source)
            request = request_for(Path("preview.png"), bbox)
            request_path = temp / "extraction-request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            output = temp / "output"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "extract_assets.py"),
                    "--request",
                    str(request_path),
                    "--output-dir",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue((output / "assets" / "icon_001.png").is_file())
            self.assertTrue((output / "masks" / "icon_001_mask.png").is_file())
            self.assertTrue((output / "extraction-result.json").is_file())


if __name__ == "__main__":
    unittest.main()
