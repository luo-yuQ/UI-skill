from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
EXPERIMENTS = ROOT / "experiments"
for directory in (SCRIPTS, EXPERIMENTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import direct_asset_discovery_probe as probe  # noqa: E402


def asset_response(
    *,
    analysis_size: tuple[int, int] = (1024, 1536),
    bbox: dict[str, int] | None = None,
) -> dict[str, Any]:
    width, height = analysis_size
    return {
        "analysis_image_size": {"width": width, "height": height},
        "assets": [
            {
                "id": "asset_001",
                "label": "independent visual symbol",
                "taxonomy": "icon",
                "bbox": bbox or {"x": 80, "y": 160, "width": 240, "height": 320},
                "partial": False,
                "confidence": 0.95,
            }
        ],
    }


class FakeVLMClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def infer_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return json.loads(json.dumps(self.response))


class DirectAssetDiscoveryProbeTests(unittest.TestCase):
    def test_cli_parser_accepts_image_output_model_and_runs(self):
        args = probe.build_parser().parse_args(
            [
                "--image",
                "clean.png",
                "--output-dir",
                "runs/direct",
                "--model",
                "cli-model",
                "--runs",
                "3",
            ]
        )
        self.assertEqual(Path("clean.png"), args.image)
        self.assertEqual(Path("runs/direct"), args.output_dir)
        self.assertEqual("cli-model", args.model)
        self.assertEqual(3, args.runs)

    def test_prompt_is_generic_and_uses_frozen_taxonomy(self):
        taxonomy = probe.load_frozen_taxonomy()
        prompt = probe.build_user_prompt(taxonomy, (1024, 1536))
        for value in taxonomy:
            self.assertIn(value, prompt)
        for page_specific_name in ("potion", "crystal", "chest", "wheel", "gift"):
            self.assertNotIn(page_specific_name, prompt.lower())
        self.assertIn("Do not construct a component tree", prompt)
        self.assertIn("Never use normalized", prompt)

    def test_bbox_mapping_reuses_stage2a_four_edge_transform(self):
        source_size = (832, 1248)
        analysis_size = (1024, 1536)
        result = probe.build_direct_assets(
            asset_response(),
            source_size,
            analysis_size,
        )
        self.assertEqual(0.8125, source_size[0] / analysis_size[0])
        self.assertEqual(0.8125, source_size[1] / analysis_size[1])
        self.assertEqual(
            {"x": 65, "y": 130, "width": 195, "height": 260},
            result["assets"][0]["bbox_source"],
        )

    def test_canonical_output_contains_analysis_and_source_bboxes(self):
        result = probe.build_direct_assets(
            asset_response(),
            (832, 1248),
            (1024, 1536),
        )
        self.assertEqual("0.1", result["schema_version"])
        self.assertEqual(
            {"width": 832, "height": 1248},
            result["source_image_size"],
        )
        self.assertEqual(
            {"width": 1024, "height": 1536},
            result["analysis_image_size"],
        )
        asset = result["assets"][0]
        self.assertEqual(
            {"x": 80, "y": 160, "width": 240, "height": 320},
            asset["bbox_analysis"],
        )
        self.assertNotIn("bbox", asset)

    def test_overlay_files_are_generated_for_both_coordinate_spaces(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            image_path = directory / "image.png"
            Image.new("RGB", (64, 64), "white").save(image_path)
            document = probe.build_direct_assets(
                asset_response(
                    analysis_size=(64, 64),
                    bbox={"x": 8, "y": 10, "width": 20, "height": 18},
                ),
                (64, 64),
                (64, 64),
            )
            analysis_overlay = directory / "overlay-analysis.png"
            source_overlay = directory / "overlay-source.png"
            probe.render_overlay(
                image_path,
                document,
                analysis_overlay,
                bbox_field="bbox_analysis",
            )
            probe.render_overlay(
                image_path,
                document,
                source_overlay,
                bbox_field="bbox_source",
            )
            self.assertTrue(analysis_overlay.is_file())
            self.assertTrue(source_overlay.is_file())
            with Image.open(analysis_overlay) as overlay:
                self.assertEqual((64, 64), overlay.size)
                self.assertNotEqual((255, 255, 255), overlay.getpixel((8, 10)))

    def test_runs_below_one_fail_before_configuration_or_network(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.main(
                [
                    "--image",
                    "unused.png",
                    "--output-dir",
                    "unused",
                    "--runs",
                    "0",
                ]
            )
        self.assertEqual(1, code)
        self.assertIn("--runs must be at least 1", stderr.getvalue())

    def test_multiple_runs_make_independent_calls_and_write_summary(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            source = directory / "clean.png"
            output = directory / "output"
            Image.new("RGB", (32, 48), "white").save(source)
            client = FakeVLMClient(asset_response())
            summary = probe.run_experiment(
                source,
                output,
                client=client,
                model="test-model",
                runs=3,
            )
            self.assertEqual(3, len(client.calls))
            self.assertEqual(
                [
                    {"run": 1, "asset_count": 1},
                    {"run": 2, "asset_count": 1},
                    {"run": 3, "asset_count": 1},
                ],
                summary["results"],
            )
            for run_number in range(1, 4):
                run_dir = output / f"run-{run_number:03d}"
                self.assertTrue((run_dir / "raw-response.json").is_file())
                self.assertTrue((run_dir / "direct-assets.json").is_file())
                self.assertTrue((run_dir / "overlay-analysis.png").is_file())
                self.assertTrue((run_dir / "overlay-source.png").is_file())
                self.assertTrue((run_dir / "run-metadata.json").is_file())
            written_summary = json.loads(
                (output / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(3, written_summary["runs"])


if __name__ == "__main__":
    unittest.main()
