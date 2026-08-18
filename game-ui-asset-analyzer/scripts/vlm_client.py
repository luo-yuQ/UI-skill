#!/usr/bin/env python3
"""Provider-neutral VLM client boundary for Stage2-A visual inference."""

from __future__ import annotations

import json
import os
import re
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised only without dependency
    requests = None  # type: ignore[assignment]


DEFAULT_MAX_OUTPUT_TOKENS = 4000
RESPONSES_PATH = "/v1/responses"
SAFE_ERROR_BODY_LIMIT = 500


class VLMError(RuntimeError):
    """Base error with a stable machine-readable classification prefix."""

    code = "vlm_error"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class VLMTransportError(VLMError):
    code = "vlm_transport_error"


class VLMResponseParseError(VLMError):
    code = "vlm_response_parse_error"


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
    def from_env(cls) -> "VLMClientConfig":
        base_url = os.environ.get("STAGE2A_VLM_BASE_URL", "").strip()
        api_key = os.environ.get("STAGE2A_VLM_API_KEY", "").strip()
        model = os.environ.get("STAGE2A_VLM_MODEL", "").strip()
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
    """Find the first assistant message output_text without positional assumptions."""

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
    raise VLMResponseParseError("Responses API response contains no output_text")


def _safe_provider_body(value: Any, api_key: str) -> str:
    text = value if isinstance(value, str) else ""
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", text)
    return " ".join(text.split())[:SAFE_ERROR_BODY_LIMIT]


def parse_json_object(response_text: str) -> dict[str, Any]:
    """Parse provider response text without repairing or extracting partial JSON."""

    try:
        result = json.loads(response_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VLMResponseParseError("model response is not valid JSON") from exc
    if not isinstance(result, dict):
        raise VLMResponseParseError("model response JSON must be an object")
    return result


class ResponsesAPIVLMClient:
    """Concrete client for the verified POST /v1/responses Provider contract."""

    def __init__(
        self,
        config: VLMClientConfig,
        *,
        session: Any | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise VLMConfigurationError("max_output_tokens must be a positive integer")
        if session is None:
            if requests is None:
                raise VLMConfigurationError(
                    "the requests package is required for production VLM execution"
                )
            session = requests.Session()
        self.config = config
        self.endpoint = build_responses_endpoint(config.base_url)
        self.session = session
        self.max_output_tokens = max_output_tokens

    def infer_json(
        self,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # The verified Provider contract does not include structured-output fields.
        del response_schema
        payload = {
            "model": self.config.model,
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
            "max_output_tokens": self.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Stage2A-VLMClient/0.1",
            "Accept-Encoding": "identity",
        }
        try:
            response = self.session.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self.config.timeout,
            )
        except Exception as exc:
            raise VLMTransportError(type(exc).__name__) from None

        status_code = getattr(response, "status_code", None)
        if type(status_code) is not int:
            raise VLMTransportError("Provider response has no HTTP status code")
        if status_code < 200 or status_code >= 300:
            body = _safe_provider_body(getattr(response, "text", ""), self.config.api_key)
            detail = f"HTTP {status_code}"
            if body:
                detail += f": {body}"
            raise VLMTransportError(detail)
        response_text = getattr(response, "text", "")
        if status_code == 204:
            raise VLMTransportError(
                "Provider returned HTTP 204 with no response body"
            )
        if not isinstance(response_text, str) or not response_text.strip():
            raise VLMTransportError("Provider returned an empty response body")
        try:
            provider_response = json.loads(response_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise VLMResponseParseError(
                "Responses API response body is not valid JSON"
            ) from exc
        output_text = extract_output_text(provider_response).strip()
        return parse_json_object(output_text)


def create_configured_vlm_client(config: VLMClientConfig) -> VLMClient:
    """Build the concrete verified Responses API client without fallback."""

    return ResponsesAPIVLMClient(config)
