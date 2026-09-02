from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
EXPERIMENTS = ROOT / "experiments"
for directory in (SCRIPTS, EXPERIMENTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import direct_asset_discovery_probe as probe  # noqa: E402
from vlm_client import (  # noqa: E402
    ChatCompletionsVLMClient,
    VLMClientConfig,
    VLMResponseTruncatedError,
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


class FakeResponse:
    def __init__(self, body: Any, status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **copy.deepcopy(kwargs)})
        return self.response


def chat_completion_body(content: str) -> dict[str, Any]:
    return {
        "object": "chat.completion",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": content},
            }
        ],
    }


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

    def test_main_builds_production_client_with_probe_parameters(self):
        config = VLMClientConfig(
            base_url="https://provider.example",
            api_key="secret",
            model="test-model",
        )
        summary = {
            "runs": 1,
            "results": [{"run": 1, "asset_count": 0}],
        }
        expected_config = replace(
            config,
            api_mode="chat_completions",
            thinking_policy="omit",
        )
        with patch.object(
            probe.VLMClientConfig,
            "from_env",
            return_value=config,
        ), patch.object(
            probe,
            "create_configured_vlm_client",
            return_value=object(),
        ) as create_client, patch.object(
            probe,
            "run_experiment",
            return_value=summary,
        ):
            code = probe.main(
                ["--image", "unused.png", "--output-dir", "unused"]
            )
        self.assertEqual(0, code)
        create_client.assert_called_once_with(
            expected_config,
            max_tokens=probe.DIRECT_ASSET_DISCOVERY_MAX_TOKENS,
        )
        self.assertEqual(12000, probe.DIRECT_ASSET_DISCOVERY_MAX_TOKENS)

    def test_wire_body_omits_thinking_and_keeps_other_fields(self):
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            image = directory / "analysis.png"
            Image.new("RGB", (16, 8), "navy").save(image)
            session = FakeSession(
                FakeResponse(chat_completion_body(json.dumps(asset_response())))
            )
            client = ChatCompletionsVLMClient(
                VLMClientConfig(
                    base_url="https://provider.example",
                    api_key="unit-test-secret",
                    model="glm-5.3-flash",
                    api_mode="chat_completions",
                    thinking_policy="omit",
                ),
                session=session,
                max_tokens=probe.DIRECT_ASSET_DISCOVERY_MAX_TOKENS,
            )
            client.infer_json(
                image_path=image,
                system_prompt="system prompt",
                user_prompt="user prompt",
                response_schema={"type": "object"},
            )
        self.assertEqual(1, len(session.calls))
        self.assertEqual(
            "https://provider.example/v1/chat/completions",
            session.calls[0]["url"],
        )
        payload = session.calls[0]["json"]
        self.assertNotIn("thinking", payload)
        self.assertNotIn("stream", payload)
        self.assertEqual("glm-5.3-flash", payload["model"])
        self.assertEqual(0, payload["temperature"])
        self.assertEqual(1, payload["top_p"])
        self.assertEqual(
            probe.DIRECT_ASSET_DISCOVERY_MAX_TOKENS, payload["max_tokens"]
        )
        self.assertEqual(12000, probe.DIRECT_ASSET_DISCOVERY_MAX_TOKENS)
        self.assertEqual(
            {"role": "system", "content": "system prompt"},
            payload["messages"][0],
        )
        user_content = payload["messages"][1]["content"]
        self.assertEqual({"type": "text", "text": "user prompt"}, user_content[0])
        self.assertEqual("image_url", user_content[1]["type"])
        self.assertTrue(
            user_content[1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )
        self.assertEqual(
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "direct_asset_discovery",
                    "schema": {"type": "object"},
                    "strict": True,
                },
            },
            payload["response_format"],
        )

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
            probe.run_experiment(
                source,
                output,
                client=client,
                model="test-model",
            )
            self.assertEqual(
                envelope,
                json.loads((output / "raw-response.json").read_text(encoding="utf-8")),
            )

    def test_truncated_provider_envelope_is_written_before_error_propagates(self):
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
                error=VLMResponseTruncatedError(
                    "model response reached token limit before producing final content"
                ),
            )
            with self.assertRaises(VLMResponseTruncatedError):
                probe.run_experiment(
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
