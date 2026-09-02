from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from vlm_client import (  # noqa: E402
    ChatCompletionsVLMClient,
    ResponsesAPIVLMClient,
    VLMClientConfig,
    VLMResponseParseError,
    VLMResponseTruncatedError,
    build_chat_completions_endpoint,
)


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


def chat_body(content: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "object": "chat.completion",
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ],
    }


class ChatCompletionsVLMClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory()
        self.addCleanup(self.context.cleanup)
        self.base = Path(self.context.name)
        self.image = self.base / "analysis.png"
        Image.new("RGB", (16, 8), "navy").save(self.image)
        self.config = VLMClientConfig(
            base_url="https://relay.example.test",
            api_key="unit-test-secret",
            model="glm-5.3-flash",
            timeout=60.0,
        )
        self.schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["analysis_image_size", "assets"],
            "properties": {
                "analysis_image_size": {"type": "object"},
                "assets": {"type": "array"},
            },
        }

    def client(
        self, body: dict[str, Any]
    ) -> tuple[ChatCompletionsVLMClient, FakeSession]:
        session = FakeSession(FakeResponse(body))
        client = ChatCompletionsVLMClient(
            self.config,
            session=session,
            max_tokens=12000,
            thinking={"type": "disabled"},
        )
        return client, session

    def infer(self, client: ChatCompletionsVLMClient) -> dict[str, Any]:
        return client.infer_json(
            self.image,
            "system prompt",
            "user prompt",
            self.schema,
        )

    def test_endpoint_builder_and_request_use_chat_completions(self) -> None:
        self.assertEqual(
            "https://relay.example.test/v1/chat/completions",
            build_chat_completions_endpoint("https://relay.example.test"),
        )
        self.assertEqual(
            "https://relay.example.test/v1/chat/completions",
            build_chat_completions_endpoint("https://relay.example.test/v1"),
        )
        client, session = self.client(
            chat_body('{"analysis_image_size": {}, "assets": []}')
        )
        self.infer(client)
        self.assertEqual(
            "https://relay.example.test/v1/chat/completions",
            session.calls[0]["url"],
        )
        self.assertNotIn("/v1/responses", session.calls[0]["url"])

    def test_request_uses_chat_envelope_multimodal_image_and_schema(self) -> None:
        client, session = self.client(
            chat_body('{"analysis_image_size": {}, "assets": []}')
        )
        self.infer(client)
        payload = session.calls[0]["json"]
        self.assertEqual("glm-5.3-flash", payload["model"])
        self.assertEqual(0, payload["temperature"])
        self.assertEqual(1, payload["top_p"])
        self.assertEqual(12000, payload["max_tokens"])
        self.assertEqual({"type": "disabled"}, payload["thinking"])
        self.assertNotIn("instructions", payload)
        self.assertNotIn("input", payload)
        self.assertNotIn("max_output_tokens", payload)
        self.assertEqual(
            {"role": "system", "content": "system prompt"},
            payload["messages"][0],
        )
        user_content = payload["messages"][1]["content"]
        self.assertEqual({"type": "text", "text": "user prompt"}, user_content[0])
        self.assertEqual("image_url", user_content[1]["type"])
        self.assertEqual(["url"], list(user_content[1]["image_url"]))
        self.assertTrue(
            user_content[1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )
        self.assertNotIn("input_image", json.dumps(payload))
        self.assertEqual(
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "direct_asset_discovery",
                    "schema": self.schema,
                    "strict": True,
                },
            },
            payload["response_format"],
        )

    def test_success_reads_choices_message_content(self) -> None:
        expected = {
            "analysis_image_size": {"width": 16, "height": 8},
            "assets": [],
        }
        client, _ = self.client(chat_body(json.dumps(expected)))
        self.assertEqual(expected, self.infer(client))

    def test_finish_reason_length_raises_truncated_and_preserves_raw_response(self) -> None:
        body = chat_body("", finish_reason="length")
        body["choices"][0]["message"]["reasoning_content"] = "debug only"
        client, _ = self.client(body)
        with self.assertRaises(VLMResponseTruncatedError):
            self.infer(client)
        self.assertEqual(body, client.get_last_provider_response())

    def test_empty_content_is_parse_error_and_reasoning_is_not_fallback(self) -> None:
        body = chat_body("")
        body["choices"][0]["message"]["reasoning_content"] = '{"ignored": true}'
        client, _ = self.client(body)
        with self.assertRaisesRegex(
            VLMResponseParseError, "no final message content"
        ):
            self.infer(client)
        self.assertEqual(body, client.get_last_provider_response())

    def test_json_content_must_be_an_object(self) -> None:
        client, _ = self.client(chat_body("[]"))
        with self.assertRaisesRegex(
            VLMResponseParseError, "JSON must be an object"
        ):
            self.infer(client)

    def test_existing_responses_client_still_targets_responses(self) -> None:
        session = FakeSession(
            FakeResponse(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": '{"ok": true}'}
                            ],
                        }
                    ]
                }
            )
        )
        client = ResponsesAPIVLMClient(self.config, session=session)
        self.assertEqual(
            {"ok": True},
            client.infer_json(self.image, "system", "user", self.schema),
        )
        self.assertEqual(
            "https://relay.example.test/v1/responses", session.calls[0]["url"]
        )
        payload = session.calls[0]["json"]
        self.assertIn("instructions", payload)
        self.assertIn("input", payload)
        self.assertNotIn("messages", payload)


if __name__ == "__main__":
    unittest.main()
