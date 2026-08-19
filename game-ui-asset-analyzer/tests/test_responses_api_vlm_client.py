from __future__ import annotations

import base64
import copy
import contextlib
import io
import json
import os
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

import run_recursive_runtime  # noqa: E402
import vlm_client  # noqa: E402
from production_visual_adapter import (  # noqa: E402
    ProductionVisualAdapter,
    StrategySchemaValidationError,
    build_production_runtime_adapters,
)
from recursive_runtime import RecursiveRuntime, RuntimeConfig  # noqa: E402
from vlm_client import (  # noqa: E402
    DEFAULT_MAX_OUTPUT_TOKENS,
    RECOVERABLE_HTTP_STATUS_CODES,
    ResponsesAPIVLMClient,
    TRANSPORT_MAX_ATTEMPTS,
    TRANSPORT_RETRY_WAIT_SECONDS,
    VLMClientConfig,
    VLMConfigurationError,
    VLMResponseParseError,
    VLMTransportError,
    encode_image_as_data_url,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        body: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = headers or {}


class FakeSession:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
        events: list[FakeResponse | Exception] | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.events = list(events) if events is not None else None
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **copy.deepcopy(kwargs)})
        if self.events is not None:
            if not self.events:
                raise AssertionError("fake transport received an unexpected request")
            event = self.events.pop(0)
            if isinstance(event, Exception):
                raise event
            return event
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake response was not configured")
        return self.response


def responses_body(text: str, *, prefix: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "output": [
            *(prefix or []),
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ]
    }


def route_result() -> dict[str, Any]:
    return {"node_role": "asset", "confidence": 0.9, "reason": "visual evidence"}


def expand_result() -> dict[str, Any]:
    return {
        "instance_type": "slot",
        "repeat_count": 0,
        "instances": [],
        "reason": "no complete repeated instance",
    }


class ResponsesAPIVLMClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.png = self.base / "analysis-image.png"
        Image.new("RGB", (1024, 512), "navy").save(self.png)
        self.config = VLMClientConfig(
            base_url="https://provider.example",
            api_key="unit-test-secret",
            model="verified-vision-model",
            timeout=12.5,
        )

    def client(
        self,
        *,
        body: Any | None = None,
        status: int = 200,
        error: Exception | None = None,
        config: VLMClientConfig | None = None,
        events: list[FakeResponse | Exception] | None = None,
    ) -> tuple[ResponsesAPIVLMClient, FakeSession]:
        if body is None and events is None:
            body = responses_body('{"ok": true}')
        response = FakeResponse(status, body) if events is None else None
        session = FakeSession(response, error=error, events=events)
        return ResponsesAPIVLMClient(config or self.config, session=session), session

    def infer(self, client: ResponsesAPIVLMClient) -> dict[str, Any]:
        return client.infer_json(
            self.png,
            "system instruction",
            "task prompt",
            response_schema={"type": "object"},
        )

    def test_t01_endpoint_is_v1_responses(self):
        client, _ = self.client()
        self.assertEqual("https://provider.example/v1/responses", client.endpoint)

    def test_t02_trailing_slash_does_not_create_double_slash(self):
        config = VLMClientConfig("https://provider.example/", "secret", "model")
        client, _ = self.client(config=config)
        self.assertEqual("https://provider.example/v1/responses", client.endpoint)

    def test_t03_base_url_with_v1_is_rejected(self):
        config = VLMClientConfig("https://provider.example/v1", "secret", "model")
        with self.assertRaisesRegex(VLMConfigurationError, "must not include the /v1"):
            ResponsesAPIVLMClient(config, session=FakeSession())

    def test_t04_bearer_authorization_header(self):
        client, session = self.client()
        self.infer(client)
        self.assertEqual("Bearer unit-test-secret", session.calls[0]["headers"]["Authorization"])

    def test_t05_content_type_is_application_json(self):
        client, session = self.client()
        self.infer(client)
        self.assertEqual("application/json", session.calls[0]["headers"]["Content-Type"])

    def test_request_accepts_application_json(self):
        client, session = self.client()
        self.infer(client)
        self.assertEqual("application/json", session.calls[0]["headers"]["Accept"])

    def test_request_uses_stable_stage2a_user_agent(self):
        client, session = self.client()
        self.infer(client)
        self.assertEqual(
            "Stage2A-VLMClient/0.1", session.calls[0]["headers"]["User-Agent"]
        )

    def test_request_disables_compressed_provider_responses(self):
        client, session = self.client()
        self.infer(client)
        self.assertEqual("identity", session.calls[0]["headers"]["Accept-Encoding"])

    def test_t06_model_comes_from_config(self):
        client, session = self.client()
        self.infer(client)
        self.assertEqual("verified-vision-model", session.calls[0]["json"]["model"])

    def test_t07_instructions_contains_system_prompt(self):
        client, session = self.client()
        self.infer(client)
        self.assertEqual("system instruction", session.calls[0]["json"]["instructions"])

    def test_t08_user_prompt_enters_input_text(self):
        client, session = self.client()
        self.infer(client)
        content = session.calls[0]["json"]["input"][0]["content"]
        self.assertEqual({"type": "input_text", "text": "task prompt"}, content[0])

    def test_t09_png_becomes_png_data_url(self):
        expected = base64.b64encode(self.png.read_bytes()).decode("ascii")
        self.assertEqual(
            f"data:image/png;base64,{expected}", encode_image_as_data_url(self.png)
        )

    def test_t10_jpeg_and_jpg_become_jpeg_data_urls(self):
        for suffix in (".jpeg", ".jpg"):
            path = self.base / f"image{suffix}"
            path.write_bytes(b"jpeg-bytes")
            self.assertEqual(
                "data:image/jpeg;base64,anBlZy1ieXRlcw==",
                encode_image_as_data_url(path),
            )

    def test_t11_body_contains_verified_message_and_image_shape(self):
        client, session = self.client()
        self.infer(client)
        item = session.calls[0]["json"]["input"][0]
        self.assertEqual(("message", "user"), (item["type"], item["role"]))
        self.assertEqual(["input_text", "input_image"], [part["type"] for part in item["content"]])
        self.assertNotIn("response_format", session.calls[0]["json"])
        self.assertNotIn("text", session.calls[0]["json"])

    def test_t12_max_output_tokens_is_4000(self):
        client, session = self.client()
        self.infer(client)
        self.assertEqual(DEFAULT_MAX_OUTPUT_TOKENS, session.calls[0]["json"]["max_output_tokens"])
        self.assertEqual(4000, DEFAULT_MAX_OUTPUT_TOKENS)

    def test_t13_normal_response_extracts_output_text(self):
        client, _ = self.client(body=responses_body('  {"value": 3}  '))
        self.assertEqual({"value": 3}, self.infer(client))

    def test_t14_earlier_non_message_output_is_ignored(self):
        client, _ = self.client(
            body=responses_body('{"ok": true}', prefix=[{"type": "function_call", "name": "ignored"}])
        )
        self.assertEqual({"ok": True}, self.infer(client))

    def test_t15_other_message_content_types_are_ignored(self):
        body = {
            "output": [{
                "type": "message",
                "content": [
                    {"type": "refusal", "refusal": "ignored"},
                    {"type": "output_text", "text": '{"ok": true}'},
                ],
            }]
        }
        client, _ = self.client(body=body)
        self.assertEqual({"ok": True}, self.infer(client))

    def test_t16_missing_output_text_is_parse_error(self):
        client, _ = self.client(body={"output": [{"type": "function_call"}]})
        with self.assertRaisesRegex(VLMResponseParseError, "vlm_response_parse_error"):
            self.infer(client)

    def test_t17_valid_output_text_json_returns_dict(self):
        client, _ = self.client(body=responses_body('{"node_role": "asset"}'))
        self.assertEqual({"node_role": "asset"}, self.infer(client))

    def test_t18_invalid_output_text_json_is_parse_error(self):
        client, session = self.client(body=responses_body("```json\n{}\n```"))
        with patch("vlm_client.time.sleep") as sleep:
            with self.assertRaisesRegex(VLMResponseParseError, "vlm_response_parse_error"):
                self.infer(client)
        self.assertEqual(1, len(session.calls))
        sleep.assert_not_called()

    def test_t19_timeout_is_transport_error(self):
        client, session = self.client(error=TimeoutError("provider timeout"))
        with patch("vlm_client.time.sleep") as sleep:
            with self.assertRaisesRegex(
                VLMTransportError, "attempts=3/3"
            ):
                self.infer(client)
        self.assertEqual(3, len(session.calls))
        self.assertEqual([5, 5], [call.args[0] for call in sleep.call_args_list])

    def test_t20_connection_failure_is_transport_error(self):
        client, session = self.client(error=ConnectionError("connection failed"))
        with patch("vlm_client.time.sleep") as sleep:
            with self.assertRaisesRegex(
                VLMTransportError, "attempts=3/3"
            ):
                self.infer(client)
        self.assertEqual(3, len(session.calls))
        self.assertEqual([5, 5], [call.args[0] for call in sleep.call_args_list])

    def test_t21_http_401_is_transport_error(self):
        client, _ = self.client(status=401, body="unauthorized")
        with self.assertRaisesRegex(VLMTransportError, "HTTP 401"):
            self.infer(client)

    def test_t22_http_500_is_transport_error(self):
        client, _ = self.client(status=500, body="provider unavailable")
        with self.assertRaisesRegex(VLMTransportError, "HTTP 500"):
            self.infer(client)

    def test_http_502_is_transport_error(self):
        client, session = self.client(status=502, body="Proxy request failed")
        with patch("vlm_client.time.sleep") as sleep:
            with self.assertRaisesRegex(VLMTransportError, "attempts=3/3"):
                self.infer(client)
        self.assertEqual(3, len(session.calls))
        self.assertEqual([5, 5], [call.args[0] for call in sleep.call_args_list])

    def test_transport_retry_v01_constants_and_status_classification(self):
        self.assertEqual(3, TRANSPORT_MAX_ATTEMPTS)
        self.assertEqual(5, TRANSPORT_RETRY_WAIT_SECONDS)
        self.assertEqual({429, 502, 503, 504}, set(RECOVERABLE_HTTP_STATUS_CODES))

    def test_transport_retry_502_then_success(self):
        client, session = self.client(
            events=[
                FakeResponse(502, '{"error":"Proxy request failed"}'),
                FakeResponse(200, responses_body('{"value": 2}')),
            ]
        )
        with patch("vlm_client.time.sleep") as sleep:
            self.assertEqual({"value": 2}, self.infer(client))
        self.assertEqual(2, len(session.calls))
        sleep.assert_called_once_with(5)
        self.assertEqual(session.calls[0]["json"], session.calls[1]["json"])
        self.assertEqual(session.calls[0]["headers"], session.calls[1]["headers"])
        self.assertEqual(session.calls[0]["timeout"], session.calls[1]["timeout"])

    def test_transport_retry_two_502_failures_then_success(self):
        client, session = self.client(
            events=[
                FakeResponse(502, "proxy failure 1"),
                FakeResponse(502, "proxy failure 2"),
                FakeResponse(200, responses_body('{"value": 3}')),
            ]
        )
        with patch("vlm_client.time.sleep") as sleep:
            self.assertEqual({"value": 3}, self.infer(client))
        self.assertEqual(3, len(session.calls))
        self.assertEqual([5, 5], [call.args[0] for call in sleep.call_args_list])

    def test_transport_retry_three_502_failures_exhausts_without_attempt_four(self):
        client, session = self.client(
            events=[
                FakeResponse(502, "proxy failure 1"),
                FakeResponse(502, "proxy failure 2"),
                FakeResponse(502, "proxy failure 3"),
            ]
        )
        with patch("vlm_client.time.sleep") as sleep:
            with self.assertRaisesRegex(
                VLMTransportError, "HTTP 502.*attempts=3/3.*last_error=HTTP 502"
            ):
                self.infer(client)
        self.assertEqual(3, len(session.calls))
        self.assertEqual([5, 5], [call.args[0] for call in sleep.call_args_list])

    def test_transport_retry_429_then_success(self):
        client, session = self.client(
            events=[
                FakeResponse(429, "rate limited"),
                FakeResponse(200, responses_body('{"ok": true}')),
            ]
        )
        with patch("vlm_client.time.sleep") as sleep:
            self.assertEqual({"ok": True}, self.infer(client))
        self.assertEqual(2, len(session.calls))
        sleep.assert_called_once_with(5)

    def test_transport_retry_503_and_504_statuses(self):
        for status in (503, 504):
            with self.subTest(status=status):
                client, session = self.client(
                    events=[
                        FakeResponse(status, "temporary upstream failure"),
                        FakeResponse(200, responses_body('{"ok": true}')),
                    ]
                )
                with patch("vlm_client.time.sleep") as sleep:
                    self.assertEqual({"ok": True}, self.infer(client))
                self.assertEqual(2, len(session.calls))
                sleep.assert_called_once_with(5)

    @unittest.skipIf(vlm_client.requests is None, "requests package is not installed")
    def test_transport_retry_requests_timeout_then_success(self):
        timeout = vlm_client.requests.Timeout("provider timeout")
        client, session = self.client(
            events=[
                timeout,
                FakeResponse(200, responses_body('{"ok": true}')),
            ]
        )
        with patch("vlm_client.time.sleep") as sleep:
            self.assertEqual({"ok": True}, self.infer(client))
        self.assertEqual(2, len(session.calls))
        sleep.assert_called_once_with(5)

    @unittest.skipIf(vlm_client.requests is None, "requests package is not installed")
    def test_transport_retry_requests_connection_error_then_success(self):
        connection_error = vlm_client.requests.ConnectionError("connection reset")
        client, session = self.client(
            events=[
                connection_error,
                FakeResponse(200, responses_body('{"ok": true}')),
            ]
        )
        with patch("vlm_client.time.sleep") as sleep:
            self.assertEqual({"ok": True}, self.infer(client))
        self.assertEqual(2, len(session.calls))
        sleep.assert_called_once_with(5)

    def test_transport_retry_nonrecoverable_http_statuses_fail_once(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                client, session = self.client(status=status, body="client error")
                with patch("vlm_client.time.sleep") as sleep:
                    with self.assertRaisesRegex(VLMTransportError, f"HTTP {status}"):
                        self.infer(client)
                self.assertEqual(1, len(session.calls))
                sleep.assert_not_called()

    def test_http_204_empty_body_is_transport_error(self):
        client, _ = self.client(status=204, body="")
        with self.assertRaisesRegex(
            VLMTransportError, "Provider returned HTTP 204 with no response body"
        ):
            self.infer(client)

    def test_http_200_empty_body_is_transport_error(self):
        client, _ = self.client(status=200, body="  \r\n")
        with self.assertRaisesRegex(
            VLMTransportError, "Provider returned an empty response body"
        ):
            self.infer(client)

    def test_http_200_application_json_response_passes(self):
        response = FakeResponse(
            200,
            responses_body('{"ok": true}'),
            headers={"Content-Type": "application/json"},
        )
        session = FakeSession(response)
        client = ResponsesAPIVLMClient(self.config, session=session)
        self.assertEqual({"ok": True}, self.infer(client))

    def test_t23_secret_is_absent_from_repr_exception_and_output(self):
        secret = self.config.api_key
        client, _ = self.client(status=401, body=f"Authorization: Bearer {secret}")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(VLMTransportError) as caught:
                self.infer(client)
        for value in (repr(self.config), repr(client), str(caught.exception), stdout.getvalue(), stderr.getvalue()):
            self.assertNotIn(secret, value)

    def test_t24_production_adapter_router_passes_with_mock_http(self):
        client, _ = self.client(body=responses_body(json.dumps(route_result())))
        adapter = ProductionVisualAdapter(client)
        self.assertEqual(route_result(), adapter.route(self.png))

    def test_t25_production_adapter_expand_passes_with_mock_http(self):
        client, _ = self.client(body=responses_body(json.dumps(expand_result())))
        adapter = ProductionVisualAdapter(client)
        self.assertEqual(expand_result(), adapter.expand_instances(self.png))

    def test_t26_schema_error_remains_production_adapter_responsibility(self):
        client, session = self.client(body=responses_body('{"node_role": "asset"}'))
        adapter = ProductionVisualAdapter(client)
        with patch("vlm_client.time.sleep") as sleep:
            with self.assertRaisesRegex(
                StrategySchemaValidationError, "strategy_schema_validation_error"
            ):
                adapter.route(self.png)
        self.assertEqual(1, len(session.calls))
        sleep.assert_not_called()

    def test_t27_missing_production_config_makes_cli_fail_closed(self):
        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(stderr):
            code = run_recursive_runtime.main(
                ["--run-dir", str(self.base / "missing-config"), "--adapter", "production"]
            )
        self.assertEqual(1, code)
        self.assertIn("production VLM configuration is missing", stderr.getvalue())

    def test_t28_missing_production_config_never_falls_back(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            run_recursive_runtime, "build_interactive_adapters"
        ) as interactive:
            code = run_recursive_runtime.main(
                ["--run-dir", str(self.base / "no-fallback"), "--adapter", "production"]
            )
        self.assertEqual(1, code)
        interactive.assert_not_called()
        self.assertFalse((self.base / "no-fallback").exists())

    def test_api_key_is_not_persisted_in_runtime_manifest(self):
        client, _ = self.client(body=responses_body(json.dumps(route_result())))
        adapter = ProductionVisualAdapter(client)
        runtime = RecursiveRuntime.create(
            run_dir=self.base / "secret-manifest",
            root_node_crop=self.png,
            adapters=build_production_runtime_adapters(adapter),
            config=RuntimeConfig(validation_mode="real_image"),
        )
        self.assertEqual("complete", runtime.run())
        manifest = runtime.manifest_path.read_text(encoding="utf-8")
        self.assertNotIn(self.config.api_key, manifest)


if __name__ == "__main__":
    unittest.main()
