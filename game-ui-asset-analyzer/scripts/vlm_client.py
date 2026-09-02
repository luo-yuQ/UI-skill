#!/usr/bin/env python3
"""Provider-neutral VLM client boundary for Stage2-A visual inference."""

from __future__ import annotations

import json
import os
import re
import time
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from threading import local
from typing import Any, Protocol
from urllib.parse import urlparse

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised only without dependency
    requests = None  # type: ignore[assignment]


DEFAULT_MAX_OUTPUT_TOKENS = 4000
RESPONSES_PATH = "/v1/responses"
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
SAFE_ERROR_BODY_LIMIT = 500
TRANSPORT_MAX_ATTEMPTS = 3
TRANSPORT_RETRY_WAIT_SECONDS = 5
RECOVERABLE_HTTP_STATUS_CODES = frozenset({429, 502, 503, 504})

RECOVERABLE_TRANSPORT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
)
if requests is not None:
    RECOVERABLE_TRANSPORT_EXCEPTIONS += (
        requests.Timeout,
        requests.ConnectionError,
    )


class VLMError(RuntimeError):
    """Base error with a stable machine-readable classification prefix."""

    code = "vlm_error"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class VLMTransportError(VLMError):
    code = "vlm_transport_error"

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)


class VLMResponseParseError(VLMError):
    code = "vlm_response_parse_error"


class VLMResponseTruncatedError(VLMResponseParseError):
    code = "vlm_response_truncated"


class VLMConfigurationError(ValueError):
    """Safe production configuration error; messages never include secrets."""


class VLMClient(Protocol):
    """Return one JSON object for an isolated image-and-prompt request."""

    def infer_json(
        self,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class VLMClientConfig:
    """Provider-neutral configuration loaded without persisting its API key."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout: float = 60.0

    @classmethod
    def from_env(cls, model_override: str | None = None) -> "VLMClientConfig":
        base_url = os.environ.get("STAGE2A_VLM_BASE_URL", "").strip()
        api_key = os.environ.get("STAGE2A_VLM_API_KEY", "").strip()
        model = (
            model_override
            if model_override is not None
            else os.environ.get("STAGE2A_VLM_MODEL", "")
        ).strip()
        raw_timeout = os.environ.get("STAGE2A_VLM_TIMEOUT", "60").strip()
        missing = [
            name
            for name, value in (
                ("STAGE2A_VLM_BASE_URL", base_url),
                ("STAGE2A_VLM_API_KEY", api_key),
                ("STAGE2A_VLM_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise VLMConfigurationError(
                "production VLM configuration is missing: " + ", ".join(missing)
            )
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise VLMConfigurationError(
                "STAGE2A_VLM_BASE_URL must be an absolute HTTP(S) URL"
            )
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise VLMConfigurationError(
                "STAGE2A_VLM_TIMEOUT must be a positive number"
            ) from exc
        if timeout <= 0:
            raise VLMConfigurationError(
                "STAGE2A_VLM_TIMEOUT must be a positive number"
            )
        return cls(base_url=base_url, api_key=api_key, model=model, timeout=timeout)

    def safe_metadata(self) -> dict[str, str]:
        return {
            "client_type": "responses_api",
            "model": self.model,
        }


def build_responses_endpoint(base_url: str) -> str:
    """Normalize one Provider base URL into its verified Responses endpoint."""

    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VLMConfigurationError(
            "STAGE2A_VLM_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.query or parsed.fragment:
        raise VLMConfigurationError(
            "STAGE2A_VLM_BASE_URL must not contain a query or fragment"
        )
    if parsed.path.rstrip("/").endswith("/v1"):
        raise VLMConfigurationError(
            "STAGE2A_VLM_BASE_URL must not include the /v1 API prefix"
        )
    return normalized + RESPONSES_PATH


def build_chat_completions_endpoint(base_url: str) -> str:
    """Normalize one Provider base URL into its Chat Completions endpoint."""

    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VLMConfigurationError(
            "STAGE2A_VLM_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.query or parsed.fragment:
        raise VLMConfigurationError(
            "STAGE2A_VLM_BASE_URL must not contain a query or fragment"
        )
    if parsed.path.rstrip("/").endswith("/v1"):
        return normalized + "/chat/completions"
    return normalized + CHAT_COMPLETIONS_PATH


def encode_image_as_data_url(image_path: Path) -> str:
    """Encode a verified local PNG or JPEG as an inline Provider data URL."""

    path = Path(image_path)
    suffix = path.suffix.lower()
    if suffix == ".png":
        media_type = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        media_type = "image/jpeg"
    else:
        raise ValueError("analysis image must be PNG, JPEG, or JPG")
    encoded = b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def extract_output_text(response: Any) -> str:
    """Extract final text from Responses API or relayed Chat Completions JSON."""

    output = response.get("output") if isinstance(response, dict) else None
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content_items = item.get("content")
            if not isinstance(content_items, list):
                continue
            for content in content_items:
                if (
                    isinstance(content, dict)
                    and content.get("type") == "output_text"
                    and isinstance(content.get("text"), str)
                ):
                    return content["text"]

    choices = response.get("choices") if isinstance(response, dict) else None
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            if choice.get("finish_reason") == "length":
                usage = response.get("usage")
                completion_tokens = None
                reasoning_tokens = None
                if isinstance(usage, dict):
                    completion_tokens = usage.get("completion_tokens")
                    details = usage.get("completion_tokens_details")
                    if isinstance(details, dict):
                        reasoning_tokens = details.get("reasoning_tokens")
                diagnostics = []
                if type(completion_tokens) is int:
                    diagnostics.append(f"completion_tokens={completion_tokens}")
                if type(reasoning_tokens) is int:
                    diagnostics.append(f"reasoning_tokens={reasoning_tokens}")
                message = (
                    "model response reached token limit before producing final content"
                )
                if diagnostics:
                    message += "; " + "; ".join(diagnostics)
                raise VLMResponseTruncatedError(message)
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                content = message["content"]
                if content.strip():
                    return content
            raise VLMResponseParseError(
                "Chat Completions response contains no final message content"
            )
    raise VLMResponseParseError("Responses API response contains no output_text")


def _safe_provider_body(value: Any, api_key: str) -> str:
    text = value if isinstance(value, str) else ""
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", text)
    return " ".join(text.split())[:SAFE_ERROR_BODY_LIMIT]


def _transport_failure_detail(detail: str, attempt: int) -> str:
    return (
        f"{detail}; attempts={attempt}/{TRANSPORT_MAX_ATTEMPTS}; "
        f"last_error={detail}"
    )


def parse_json_object(response_text: str) -> dict[str, Any]:
    """Parse provider response text without repairing or extracting partial JSON."""

    try:
        text = response_text.strip()

        # remove model reasoning block
        for start_tag, end_tag in [
            ("<think>", "</think>"),
            ("<thinking>", "</thinking>")
        ]:
            if start_tag in text and end_tag in text:
                text = text.split(end_tag, 1)[1].strip()

        # remove markdown fence
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [
                line for line in lines
                if not line.strip().startswith("```")
            ]
            text = "\n".join(lines).strip()

        result = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VLMResponseParseError("model response is not valid JSON") from exc
    if not isinstance(result, dict):
        raise VLMResponseParseError("model response JSON must be an object")
    return result


class ChatCompletionsVLMClient:
    """Isolated Chat Completions client with API-level JSON Schema output."""

    def __init__(
        self,
        config: VLMClientConfig,
        *,
        session: Any | None = None,
        max_tokens: int,
        thinking: dict[str, Any],
    ) -> None:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise VLMConfigurationError("max_tokens must be a positive integer")
        if thinking != {"type": "disabled"}:
            raise VLMConfigurationError(
                "thinking must use the Chat Completions disabled object"
            )
        if session is None:
            if requests is None:
                raise VLMConfigurationError(
                    "the requests package is required for production VLM execution"
                )
            self._session_factory = requests.Session
            self._session_local = local()
        else:
            self._session_factory = None
            self._session_local = None
        self.config = config
        self.endpoint = build_chat_completions_endpoint(config.base_url)
        self.session = session
        self.max_tokens = max_tokens
        self.thinking = dict(thinking)
        self._response_local = local()

    def get_last_provider_response(self) -> Any | None:
        """Return the raw decoded provider envelope for the current thread."""

        return getattr(self._response_local, "provider_response", None)

    def _get_session(self) -> Any:
        """Return the injected session or one production session per worker thread."""

        if self.session is not None:
            return self.session
        if self._session_factory is None or self._session_local is None:
            raise AssertionError("VLM session factory was not initialized")
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = self._session_factory()
            self._session_local.session = session
        return session

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
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(response_schema, dict):
            raise VLMConfigurationError(
                "Chat Completions requires a JSON response schema"
            )
        self._response_local.provider_response = None
        payload: dict[str, Any] = {
            "model": self.config.model,
            "temperature": 0,
            "top_p": 1,
            "max_tokens": self.max_tokens,
            "thinking": dict(self.thinking),
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
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Stage2A-VLMClient/0.1",
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
        self._response_local.provider_response = provider_response
        try:
            choice = provider_response["choices"][0]
            finish_reason = choice.get("finish_reason")
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise VLMResponseParseError(
                "Chat Completions response has no assistant content"
            ) from exc
        if finish_reason == "length":
            raise VLMResponseTruncatedError(
                "model response reached token limit before producing final content"
            )
        if not isinstance(content, str) or not content.strip():
            raise VLMResponseParseError(
                "Chat Completions response contains no final message content"
            )
        return parse_json_object(content)


class ResponsesAPIVLMClient:
    """Concrete client for the verified POST /v1/responses Provider contract."""

    def __init__(
        self,
        config: VLMClientConfig,
        *,
        session: Any | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_tokens: int | None = None,
        thinking: bool | None = None,
    ) -> None:
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise VLMConfigurationError("max_output_tokens must be a positive integer")
        if max_tokens is not None and (
            type(max_tokens) is not int or max_tokens <= 0
        ):
            raise VLMConfigurationError("max_tokens must be a positive integer")
        if thinking is not None and type(thinking) is not bool:
            raise VLMConfigurationError("thinking must be a boolean")
        if session is None:
            if requests is None:
                raise VLMConfigurationError(
                    "the requests package is required for production VLM execution"
                )
            self._session_factory = requests.Session
            self._session_local = local()
        else:
            self._session_factory = None
            self._session_local = None
        self.config = config
        self.endpoint = build_responses_endpoint(config.base_url)
        self.session = session
        self.max_output_tokens = max_output_tokens
        self.max_tokens = max_tokens
        self.thinking = thinking
        self._response_local = local()

    def get_last_provider_response(self) -> Any | None:
        """Return the raw decoded provider envelope for the current thread."""

        return getattr(self._response_local, "provider_response", None)

    def _get_session(self) -> Any:
        """Return the injected session or one production session per worker thread."""

        if self.session is not None:
            return self.session
        if self._session_factory is None or self._session_local is None:
            raise AssertionError("VLM session factory was not initialized")
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = self._session_factory()
            self._session_local.session = session
        return session

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
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # The relay's response_format_schema payload shape has not been verified.
        del response_schema
        self._response_local.provider_response = None
        payload: dict[str, Any] = {
            "model": self.config.model,
            "temperature": 0,
            "top_p": 1,
            "instructions": system_prompt,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_prompt},
                        {
                            "type": "input_image",
                            "image_url": encode_image_as_data_url(image_path),
                        },
                    ],
                }
            ],
        }
        if self.max_tokens is None:
            payload["max_output_tokens"] = self.max_output_tokens
        else:
            payload["max_tokens"] = self.max_tokens
        if self.thinking is not None:
            payload["thinking"] = self.thinking
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Stage2A-VLMClient/0.1",
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
                "Responses API response body is not valid JSON"
            ) from exc
        self._response_local.provider_response = provider_response
        output_text = extract_output_text(provider_response).strip()
        return parse_json_object(output_text)


def create_configured_vlm_client(
    config: VLMClientConfig,
    *,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    max_tokens: int | None = None,
    thinking: bool | None = None,
) -> VLMClient:
    """Build the concrete verified Responses API client without fallback."""

    return ResponsesAPIVLMClient(
        config,
        max_output_tokens=max_output_tokens,
        max_tokens=max_tokens,
        thinking=thinking,
    )


def create_chat_completions_vlm_client(
    config: VLMClientConfig,
    *,
    max_tokens: int,
    thinking: dict[str, Any],
) -> VLMClient:
    """Build the isolated Chat Completions client for explicit callers."""

    return ChatCompletionsVLMClient(
        config,
        max_tokens=max_tokens,
        thinking=thinking,
    )
