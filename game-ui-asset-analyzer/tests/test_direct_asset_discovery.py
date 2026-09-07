from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import direct_asset_discovery as discovery  # noqa: E402
from vlm_client import (  # noqa: E402
    VLMClientConfig,
    VLMResponseParseError,
)


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
    def __init__(
        self,
        response: dict[str, Any],
        *,
        provider_response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.provider_response = provider_response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def infer_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return json.loads(json.dumps(self.response))

    def get_last_provider_response(self) -> dict[str, Any] | None:
        return self.provider_response


class DirectAssetDiscoveryTests(unittest.TestCase):
    def test_cli_parser_accepts_image_output_model_and_runs(self):
        args = discovery.build_parser().parse_args(
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
        taxonomy = discovery.load_frozen_taxonomy()
        prompt = discovery.build_user_prompt(taxonomy, (1024, 1536))
        for value in taxonomy:
            self.assertIn(value, prompt)
        for page_specific_name in ("potion", "crystal", "chest", "wheel", "gift"):
            self.assertNotIn(page_specific_name, prompt.lower())
        self.assertIn("Do not construct a component tree", prompt)
        self.assertIn("Never use normalized", prompt)

    def test_bbox_mapping_reuses_stage2a_four_edge_transform(self):
        source_size = (832, 1248)
        analysis_size = (1024, 1536)
        result = discovery.build_direct_assets(
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
        result = discovery.build_direct_assets(
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

    def test_duplicate_asset_ids_fail_validation(self):
        response = asset_response()
        response["assets"].append(copy.deepcopy(response["assets"][0]))
        with self.assertRaises(ValueError) as context:
            discovery.build_direct_assets(response, (832, 1248), (1024, 1536))
        self.assertIn("duplicate id", str(context.exception))

    def test_text_taxonomy_fails_validation(self):
        response = asset_response()
        response["assets"][0]["taxonomy"] = "text"
        with self.assertRaises(ValueError) as context:
            discovery.build_direct_assets(response, (832, 1248), (1024, 1536))
        self.assertIn("text glyphs are excluded", str(context.exception))

    def test_out_of_bounds_bbox_fails_validation(self):
        response = asset_response(bbox={"x": 1000, "y": 1500, "width": 100, "height": 100})
        with self.assertRaises(ValueError) as context:
            discovery.build_direct_assets(response, (832, 1248), (1024, 1536))
        self.assertIn("exceeds analysis image", str(context.exception))

    def test_overlay_files_are_generated_for_both_coordinate_spaces(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            image_path = directory / "image.png"
            Image.new("RGB", (64, 64), "white").save(image_path)
            document = discovery.build_direct_assets(
                asset_response(
                    analysis_size=(64, 64),
                    bbox={"x": 8, "y": 10, "width": 20, "height": 18},
                ),
                (64, 64),
                (64, 64),
            )
            analysis_overlay = directory / "overlay-analysis.png"
            source_overlay = directory / "overlay-source.png"
            discovery.render_overlay(
                image_path,
                document,
                analysis_overlay,
                bbox_field="bbox_analysis",
            )
            discovery.render_overlay(
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
            code = discovery.main(
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

    def test_main_builds_production_client_with_model_override(self):
        config = VLMClientConfig(
            base_url="https://provider.example",
            api_key="secret",
            model="test-model",
        )
        summary = {
            "runs": 1,
            "results": [{"run": 1, "asset_count": 0}],
        }
        with patch.object(
            discovery.VLMClientConfig,
            "from_env",
            return_value=config,
        ), patch.object(
            discovery,
            "ChatCompletionsVLMClient",
        ) as client_factory, patch.object(
            discovery,
            "run_discovery",
            return_value=summary,
        ) as run_discovery:
            code = discovery.main(
                [
                    "--image",
                    "unused.png",
                    "--output-dir",
                    "unused",
                    "--model",
                    "glm-5.3-flash",
                ]
            )
        self.assertEqual(0, code)
        called_config = client_factory.call_args.args[0]
        self.assertEqual("glm-5.3-flash", called_config.model)
        self.assertEqual("https://provider.example", called_config.base_url)
        self.assertEqual(
            {"max_tokens": discovery.DIRECT_ASSET_DISCOVERY_MAX_TOKENS},
            client_factory.call_args.kwargs,
        )
        self.assertEqual(12000, discovery.DIRECT_ASSET_DISCOVERY_MAX_TOKENS)
        run_discovery.assert_called_once()

    def test_main_without_model_override_keeps_env_model(self):
        config = VLMClientConfig(
            base_url="https://provider.example",
            api_key="secret",
            model="env-model",
        )
        with patch.object(
            discovery.VLMClientConfig,
            "from_env",
            return_value=config,
        ), patch.object(
            discovery,
            "ChatCompletionsVLMClient",
        ) as client_factory, patch.object(
            discovery,
            "run_discovery",
            return_value={"runs": 1, "results": [{"run": 1, "asset_count": 0}]},
        ):
            code = discovery.main(
                ["--image", "unused.png", "--output-dir", "unused"]
            )
        self.assertEqual(0, code)
        self.assertEqual("env-model", client_factory.call_args.args[0].model)

    def test_multiple_runs_make_independent_calls_and_write_summary(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            source = directory / "clean.png"
            output = directory / "output"
            Image.new("RGB", (32, 48), "white").save(source)
            client = FakeVLMClient(asset_response())
            summary = discovery.run_discovery(
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

    def test_provider_envelope_is_written_as_raw_response(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            source = directory / "clean.png"
            output = directory / "output"
            Image.new("RGB", (32, 48), "white").save(source)
            envelope = {
                "object": "chat.completion",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(asset_response())},
                    }
                ],
            }
            client = FakeVLMClient(
                asset_response(analysis_size=(1024, 1536)),
                provider_response=envelope,
            )
            discovery.run_discovery(
                source,
                output,
                client=client,
                model="test-model",
            )
            self.assertEqual(
                envelope,
                json.loads((output / "raw-response.json").read_text(encoding="utf-8")),
            )

    def test_parse_error_provider_envelope_is_written_before_error_propagates(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            source = directory / "clean.png"
            output = directory / "output"
            Image.new("RGB", (32, 48), "white").save(source)
            envelope = {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": "",
                            "reasoning_content": "long reasoning",
                        },
                    }
                ]
            }
            client = FakeVLMClient(
                {},
                provider_response=envelope,
                error=VLMResponseParseError("model response is not valid JSON"),
            )
            with self.assertRaises(VLMResponseParseError):
                discovery.run_discovery(
                    source,
                    output,
                    client=client,
                    model="test-model",
                )
            self.assertEqual(
                envelope,
                json.loads((output / "raw-response.json").read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
