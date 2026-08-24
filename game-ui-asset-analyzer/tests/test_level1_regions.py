import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import process_level1_regions as processor
import validate_level1_regions as validator


def make_region(index, bbox, label=None):
    return {
        "id": f"region_{index:03d}",
        "node_kind": "region",
        "label": label or f"Region {index}",
        "bbox": bbox,
    }


def make_raw(size=(100, 80), regions=None):
    return {
        "schema_version": "0.1",
        "source_image": "preview.png",
        "source_size": {"width": size[0], "height": size[1]},
        "background_root": {
            "id": "background_root",
            "node_kind": "background_root",
            "requires_reconstruction": True,
        },
        "regions": regions
        if regions is not None
        else [make_region(1, {"x": 10, "y": 10, "width": 30, "height": 20})],
    }


class Level1RegionTests(unittest.TestCase):
    def process(self, raw, image_size=None, **options):
        context = tempfile.TemporaryDirectory()
        temp = Path(context.name)
        size = image_size or (
            raw["source_size"]["width"],
            raw["source_size"]["height"],
        )
        source = temp / "input" / "preview.png"
        source.parent.mkdir(parents=True)
        Image.new("RGB", size, (30, 60, 90)).save(source)
        output = temp / "analysis" / "level1-regions.json"
        crops = temp / "level1-crops"
        overlay = temp / "analysis" / "level1-overlay.png"
        result = processor.process_regions(
            raw,
            source,
            output,
            crops,
            overlay,
            **options,
        )
        return context, source, output, crops, overlay, result

    def test_a_padding_clamps_at_top_left(self):
        raw = make_raw(
            regions=[make_region(1, {"x": 0, "y": 0, "width": 20, "height": 10})]
        )
        context, _, _, _, _, result = self.process(
            raw, padding_ratio=0.5, min_output_short_side=0
        )
        self.addCleanup(context.cleanup)
        region = result["regions"][0]
        self.assertEqual(
            {"x": 0, "y": 0, "width": 30, "height": 15},
            region["analysis_bbox"],
        )
        self.assertEqual(
            {"left": 0, "top": 0, "right": 10, "bottom": 5},
            region["padding"]["actual_pixels"],
        )
        self.assertEqual(raw["regions"][0]["bbox"], region["bbox"])

    def test_b_padding_clamps_at_bottom_right(self):
        raw = make_raw(
            regions=[make_region(1, {"x": 80, "y": 60, "width": 20, "height": 20})]
        )
        context, _, _, _, _, result = self.process(
            raw, padding_ratio=0.5, min_output_short_side=0
        )
        self.addCleanup(context.cleanup)
        region = result["regions"][0]
        self.assertEqual(
            {"x": 70, "y": 50, "width": 30, "height": 30},
            region["analysis_bbox"],
        )
        self.assertEqual(
            {"left": 10, "top": 10, "right": 0, "bottom": 0},
            region["padding"]["actual_pixels"],
        )

    def test_c_overlapping_regions_are_valid_and_preserved(self):
        raw = make_raw(
            regions=[
                make_region(1, {"x": 10, "y": 10, "width": 50, "height": 40}),
                make_region(2, {"x": 40, "y": 30, "width": 50, "height": 40}),
            ]
        )
        self.assertEqual([], validator.validate_raw(raw))
        context, _, _, _, _, result = self.process(
            raw, padding_ratio=0, min_output_short_side=0
        )
        self.addCleanup(context.cleanup)
        self.assertEqual(2, len(result["regions"]))

    def test_d_small_roi_is_upscaled_with_recorded_exact_scale(self):
        raw = make_raw(
            regions=[make_region(1, {"x": 10, "y": 10, "width": 20, "height": 10})]
        )
        context, _, output, _, _, result = self.process(
            raw,
            padding_ratio=0,
            min_output_short_side=100,
            max_upscale=2,
        )
        self.addCleanup(context.cleanup)
        region = result["regions"][0]
        self.assertTrue(region["upscale"]["applied"])
        self.assertEqual(2.0, region["transform"]["scale_x"])
        self.assertEqual(2.0, region["transform"]["scale_y"])
        crop_path = output.parent / region["output_crop"]
        with Image.open(crop_path) as crop:
            self.assertEqual((40, 20), crop.size)

    def test_e_large_roi_is_not_upscaled(self):
        raw = make_raw(
            regions=[make_region(1, {"x": 5, "y": 5, "width": 80, "height": 60})]
        )
        context, _, _, _, _, result = self.process(
            raw,
            padding_ratio=0,
            min_output_short_side=50,
            max_upscale=2,
        )
        self.addCleanup(context.cleanup)
        region = result["regions"][0]
        self.assertFalse(region["upscale"]["applied"])
        self.assertEqual(1.0, region["transform"]["scale_x"])
        self.assertEqual(1.0, region["transform"]["scale_y"])

    def test_f_coordinate_transform_round_trip(self):
        transform = {
            "source_x": 20,
            "source_y": 68,
            "source_width": 820,
            "source_height": 690,
            "output_width": 1230,
            "output_height": 1035,
            "scale_x": 1.5,
            "scale_y": 1.5,
        }
        local = {"x": 33.5, "y": 49.25, "width": 120.5, "height": 80.75}
        source = processor.output_bbox_to_source(local, transform)
        round_trip = processor.source_bbox_to_output(source, transform)
        for key in local:
            self.assertTrue(math.isclose(local[key], round_trip[key], abs_tol=1e-9))

    def test_g_dynamic_region_counts(self):
        for count in (2, 3, 5):
            with self.subTest(count=count):
                regions = []
                for index in range(1, count + 1):
                    regions.append(
                        make_region(
                            index,
                            {
                                "x": (index - 1) * 10,
                                "y": (index - 1) * 5,
                                "width": 20,
                                "height": 15,
                            },
                        )
                    )
                raw = make_raw(regions=regions)
                context, _, output, _, overlay, result = self.process(
                    raw, padding_ratio=0.1, min_output_short_side=0
                )
                try:
                    self.assertEqual(count, len(result["regions"]))
                    self.assertEqual(
                        [], validator.validate_processed(result, output)
                    )
                    with Image.open(overlay) as image:
                        image.verify()
                    for region in result["regions"]:
                        with Image.open(output.parent / region["output_crop"]) as crop:
                            crop.verify()
                finally:
                    context.cleanup()

    def test_duplicate_or_malformed_region_ids_fail(self):
        raw = make_raw(
            regions=[
                make_region(1, {"x": 0, "y": 0, "width": 20, "height": 20}),
                make_region(1, {"x": 20, "y": 20, "width": 20, "height": 20}),
            ]
        )
        errors = validator.validate_raw(raw)
        self.assertTrue(any("duplicate region id" in error for error in errors))
        malformed = copy.deepcopy(raw)
        malformed["regions"][1]["id"] = "Header"
        errors = validator.validate_raw(malformed)
        self.assertTrue(any("does not match" in error for error in errors))

    def test_out_of_bounds_raw_bbox_fails_before_writing(self):
        raw = make_raw(
            regions=[make_region(1, {"x": 90, "y": 10, "width": 20, "height": 20})]
        )
        errors = validator.validate_raw(raw)
        self.assertTrue(any("exceeds source width" in error for error in errors))

    def test_processor_and_validator_clis_preserve_raw_utf8(self):
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            source = temp / "input" / "preview.png"
            source.parent.mkdir(parents=True)
            Image.new("RGB", (100, 80), "navy").save(source)
            raw_path = temp / "analysis" / "level1-regions.raw.json"
            raw_path.parent.mkdir(parents=True)
            raw_data = make_raw(
                regions=[
                    make_region(
                        1,
                        {"x": 0, "y": 0, "width": 100, "height": 30},
                        label="顶部区域",
                    )
                ]
            )
            raw_text = json.dumps(raw_data, ensure_ascii=False, indent=2) + "\n"
            raw_path.write_text(raw_text, encoding="utf-8")
            output = temp / "analysis" / "level1-regions.json"
            overlay = temp / "analysis" / "level1-overlay.png"
            crops = temp / "level1-crops"

            process_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "process_level1_regions.py"),
                    "--raw-json",
                    str(raw_path),
                    "--source-image",
                    str(source),
                    "--output-json",
                    str(output),
                    "--crops-dir",
                    str(crops),
                    "--overlay-output",
                    str(overlay),
                    "--padding-ratio",
                    "0.06",
                    "--min-output-short-side",
                    "50",
                    "--max-upscale",
                    "2",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, process_result.returncode, process_result.stderr)
            self.assertEqual(raw_text, raw_path.read_text(encoding="utf-8"))
            self.assertIn("顶部区域", output.read_text(encoding="utf-8"))

            validate_result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_level1_regions.py"),
                    str(output),
                    "--source-image",
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, validate_result.returncode, validate_result.stderr)
            self.assertIn("Valid processed Level-1 regions", validate_result.stdout)


if __name__ == "__main__":
    unittest.main()
