#!/usr/bin/env python3
"""Chat Completions VLM client for the Direct Asset Discovery production chain.

Phase 4 production client boundary. The verified production provider contract
for Direct Asset Discovery (see
``runs/20260902_direct-asset-discovery-007-production-client/raw-response.json``,
an ``object: "chat.completion"`` envelope with ``usage``) is Chat Completions
with API-level JSON Schema submission and ``thinking`` omitted. The repository
Responses API client (``vlm_client.ResponsesAPIVLMClient``) does not speak this
contract, and modifying it is out of scope for this phase, so the Chat
Completions path lives here.

Reused from the frozen main VLM boundary (zero change to ``vlm_client.py``):

- ``VLMClientConfig`` (base_url / api_key / model / timeout dataclass)
- ``VLMError`` hierarchy (``VLMTransportError`` / ``VLMResponseParseError``)
- ``encode_image_as_data_url`` (inline PNG/JPEG data URL encoding)
- ``parse_json_object`` (strict JSON decode with thinking-block and fence
  stripping; no partial-JSON repair)

Provided here:

- ``ChatCompletionsVLMClient``: POST ``{base}/v1/chat/completions`` with
  ``temperature: 0``, ``top_p: 1``, ``thinking: {"type": "omit"}``, the
  caller's ``response_schema`` as a strict ``json_schema`` response_format, and
  ``max_tokens``. Retries recoverable transport failures with the same frozen
  Transport Retry v0.1 semantics as the Responses client.
- ``get_last_provider_response``: raw provider envelope of the last call, used
  by discovery/admission to persist evidence (including truncation evidence
  saved before a ``VLMResponseTruncatedError`` propagates).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from threading import local
from typing import Any

import requests

from vlm_client import (
    VLMClientConfig,
    VLMError,
    VLMResponseParseError,
    VLMTransportError,
    _safe_provider_body,
    encode_image_as_data_url,
    parse_json_object,
)


DEFAULT_MAX_TOKENS = 4000
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
TRANSPORT_MAX_ATTEMPTS = 3
TRANSPORT_RETRY_WAIT_SECONDS = 5
RECOVERABLE_HTTP_STATUS_CODES = frozenset({429, 502, 503, 504})

RECOVERABLE_TRANSPORT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    requests.Timeout,
    requests.ConnectionError,
)


class VLMResponseTruncatedError(VLMError):
    """Provider stopped at the token limit before producing final content."""

    code = "vlm_response_truncated_error"


def build_chat_completions_endpoint(base_url: str) -> str:
    """Normalize one provider base URL into its Chat Completions endpoint."""

    normalized = base_url.strip().rstrip("/")
    if not normalized.lower().startswith(("http://", "https://")):
        raise VLMTransportError(
            "base_url must be an absolute HTTP(S) URL", retryable=False
        )
    return normalized + CHAT_COMPLETIONS_PATH


@dataclass(frozen=True)
class ChatCompletionsCallOptions:
    """Per-call wire options for the Chat Completions production contract."""

    max_tokens: int = DEFAULT_MAX_TOKENS
    thinking_policy: str = "omit"


def _transport_failure_detail(detail: str, attempt: int) -> str:
    return (
        f"{detail}; attempts={attempt}/{TRANSPORT_MAX_ATTEMPTS}; "
        f"last_error={detail}"
    )


class ChatCompletionsVLMClient:
    """Concrete client for the verified POST /v1/chat/completions contract."""

    def __init__(
        self,
        config: VLMClientConfig,
        *,
        session: Any | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        if session is None:
            self._session_factory = requests.Session
            self._session_local = local()
        else:
            self._session_factory = None
            self._session_local = None
            self.session = session
        self.config = config
        self.endpoint = build_chat_completions_endpoint(config.base_url)
        self.max_tokens = max_tokens
        self._last_provider_response: Any | None = None

    def _get_session(self) -> Any:
        """Return the injected session or one production session per thread."""

        if self._session_factory is None or self._session_local is None:
            return self.session
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = self._session_factory()
            self._session_local.session = session
        return session

    def get_last_provider_response(self) -> Any | None:
        """Return the raw provider envelope of the most recent call."""

        return self._last_provider_response

    def _post_with_transport_retry(
        self,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> Any:
        """Send one unchanged request with Transport Retry v0.1 semantics."""

        for attempt in range(1, TRANSPORT_MAX_ATTEMPTS + 1):
            status_code: int | None = None
            try:
                response = self._get_session().post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout,
                )
            except RECOVERABLE_TRANSPORT_EXCEPTIONS as exc:
                detail = type(exc).__name__
            except Exception as exc:
                detail = type(exc).__name__
                raise VLMTransportError(
                    _transport_failure_detail(detail, attempt), retryable=False
                ) from None
            else:
                status_code = getattr(response, "status_code", None)
                if type(status_code) is not int:
                    detail = "Provider response has no HTTP status code"
                    raise VLMTransportError(
                        _transport_failure_detail(detail, attempt), retryable=False
                    )
                if 200 <= status_code < 300:
                    return response
                body = _safe_provider_body(
                    getattr(response, "text", ""), self.config.api_key
                )
                detail = f"HTTP {status_code}"
                if body:
                    detail += f": {body}"
                if status_code not in RECOVERABLE_HTTP_STATUS_CODES:
                    raise VLMTransportError(
                        _transport_failure_detail(detail, attempt),
                        retryable=False,
                        status_code=status_code,
                    )

            if attempt == TRANSPORT_MAX_ATTEMPTS:
                raise VLMTransportError(
                    _transport_failure_detail(detail, attempt),
                    retryable=True,
                    status_code=status_code,
                )
            time.sleep(TRANSPORT_RETRY_WAIT_SECONDS)

        raise AssertionError("transport retry loop exhausted without a result")

    def infer_json(
        self,
        image_path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(response_schema, dict):
            raise VLMResponseParseError(
                "Chat Completions requires a JSON response schema"
            )
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "top_p": 1,
            "thinking": {"type": "omit"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": encode_image_as_data_url(image_path)
                            },
                        },
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "direct_asset_discovery",
                    "schema": response_schema,
                    "strict": True,
                },
            },
            "max_tokens": self.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "DirectAssetDiscovery-VLMClient/0.1",
            "Accept-Encoding": "identity",
        }
        response = self._post_with_transport_retry(payload=payload, headers=headers)
        status_code = getattr(response, "status_code", None)
        response_text = getattr(response, "text", "")
        if status_code == 204:
            raise VLMTransportError(
                "Provider returned HTTP 204 with no response body",
                retryable=True,
                status_code=204,
            )
        if not isinstance(response_text, str) or not response_text.strip():
            raise VLMTransportError(
                "Provider returned an empty response body",
                retryable=True,
                status_code=status_code if type(status_code) is int else None,
            )
        try:
            provider_response = json.loads(response_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise VLMResponseParseError(
                "Chat Completions response body is not valid JSON"
            ) from exc
        self._last_provider_response = provider_response

        choices = provider_response.get("choices")
        content: Any = None
        finish_reason: Any = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish_reason = choices[0].get("finish_reason")
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
        if finish_reason == "length" and (not isinstance(content, str) or not content.strip()):
            raise VLMResponseTruncatedError(
                "model response reached token limit before producing final content"
            )
        if not isinstance(content, str) or not content.strip():
            raise VLMResponseParseError(
                "Chat Completions response contains no message content"
            )
        return parse_json_object(content)
