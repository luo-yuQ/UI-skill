#!/usr/bin/env python3
"""Build a rectangular UI-text region mask from one canonical VLM response."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import cv2
import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised only without dependency
    requests = None  # type: ignore[assignment]

from ui_text_models import TextItem


DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_IMAGE_WIDTH = 1024
SCHEMA_VERSION = "route-b-v0.5-region-mask-poc"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

UI_ROLES = {
    "navigation_label",
    "button_label",
    "runtime_value",
    "body_text",
    "ordinary_title",
    "status_text",
}
ASSET_ROLES = {
    "embedded_in_artwork",
    "embedded_logo",
    "decorative_art_text",
}
SemanticRole = Literal[
    "navigation_label",
    "button_label",
    "runtime_value",
    "body_text",
    "ordinary_title",
    "status_text",
    "embedded_in_artwork",
    "embedded_logo",
    "decorative_art_text",
]

ANALYZER_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2] / "game-ui-asset-analyzer" / "scripts"
)
if str(ANALYZER_SCRIPTS_DIR) not in sys.path:
    sys.path.append(str(ANALYZER_SCRIPTS_DIR))

try:
    from prepare_analysis_input import prepare_analysis_input  # type: ignore[import-not-found]
    from vlm_client import (  # type: ignore[import-not-found]
        VLMClientConfig,
        VLMResponseParseError,
        encode_image_as_data_url,
    )
except ImportError:  # pragma: no cover - only relevant to incomplete deployments
    prepare_analysis_input = None
    VLMClientConfig = None
    encode_image_as_data_url = None

    class VLMResponseParseError(RuntimeError):
        """Fallback used only when the repository client cannot be imported."""


SYSTEM_PROMPT = """You are reviewing all visible text regions in a complete game UI screenshot.

You are given OCR candidates as hints, but they are not authoritative.

Produce the final canonical list of visible text regions.

You must:
- confirm OCR text that is correct,
- correct OCR text or bounding boxes when necessary,
- add clearly visible text missed by OCR,
- omit false OCR detections,
- determine whether each text region is owned by the UI information layer or by a visual asset.

Use the full screenshot and visual context.

UI-owned text is visually independent interface information that could reasonably be rendered or updated separately.

Asset-owned text is visually integrated into artwork, logos, emblems, illustration, branding, or decorative composition.

Return a tight bounding box around the visible text itself in analysis-image coordinates.

Do not use OCR bounding boxes as mandatory final boxes.
Do not output source-image coordinates.
Do not output remove/preserve decisions.
Do not invent semantic roles outside the provided closed set.

Allowed UI-owned semantic roles:
- navigation_label
- button_label
- runtime_value
- body_text
- ordinary_title
- status_text

Allowed asset-owned semantic roles:
- embedded_in_artwork
- embedded_logo
- decorative_art_text

Every ui_owned region must use a UI-owned role. Every asset_owned region must use an asset-owned role.

Return exactly one JSON object.

The only allowed top-level key is "texts".

Required top-level structure:

{
  "texts": [...]
}

Do not use alternative top-level keys such as:
"text_regions",
"regions",
"items",
or "detected_texts".

Return strict JSON matching the schema, with no Markdown or additional commentary."""


SCHEMA_RETRY_INSTRUCTION = """Your previous response did not match the required JSON schema.

Return exactly:
{
  "texts": [...]
}

The only allowed top-level key is "texts".
Return JSON only."""


class RegionMaskError(RuntimeError):
    """Base error for this isolated PoC."""


class RegionMaskInputError(RegionMaskError):
    """Raised when local inputs violate the coordinate contract."""


class RegionMaskClientError(RegionMaskError):
    """Raised when a VLM request fails."""


class RegionMaskResponseError(RegionMaskError):
    """Raised when any part of the VLM response is invalid."""


class VLMClient(Protocol):
    def infer_json(
        self,
        *,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        """Return one decoded JSON-compatible response."""


def _build_chat_completions_endpoint(base_url: str) -> str:
    """Normalize a relay base URL to its Chat Completions endpoint."""

    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RegionMaskClientError(
            "OPENAI_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.query or parsed.fragment:
        raise RegionMaskClientError(
            "OPENAI_BASE_URL must not contain a query or fragment"
        )
    if parsed.path.rstrip("/").endswith("/v1"):
        return normalized + "/chat/completions"
    return normalized + CHAT_COMPLETIONS_PATH


class ChatCompletionsSchemaVLMClient:
    """Chat Completions VLM client with API-level JSON Schema submission."""

    def __init__(self, config: Any, *, session: Any | None = None) -> None:
        if session is None:
            if requests is None:
                raise RegionMaskClientError(
                    "The requests package is required for Chat Completions"
                )
            session = requests.Session()
        self.config = config
        self.session = session
        self.endpoint = _build_chat_completions_endpoint(config.base_url)

    def infer_json(
        self,
        *,
        image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        if not isinstance(response_schema, dict):
            raise RegionMaskClientError(
                "Chat Completions requires a JSON response schema"
            )
        if encode_image_as_data_url is None:
            raise RegionMaskClientError(
                "Repository image data-URL helper is unavailable"
            )
        payload = {
            "model": self.config.model,
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
                    "name": "canonical_text_response",
                    "strict": True,
                    "schema": response_schema,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "curl/8.16.0",
        }
        try:
            response = self.session.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self.config.timeout,
            )
        except Exception as exc:
            raise RegionMaskClientError(
                f"Chat Completions transport failed: {type(exc).__name__}"
            ) from exc

        status_code = getattr(response, "status_code", None)
        response_headers = getattr(response, "headers", {})
        content_type = (
            response_headers.get("Content-Type", "<missing>")
            if hasattr(response_headers, "get")
            else "<missing>"
        )
        content_encoding = (
            response_headers.get("Content-Encoding", "<missing>")
            if hasattr(response_headers, "get")
            else "<missing>"
        )
        transfer_encoding = (
            response_headers.get("Transfer-Encoding", "<missing>")
            if hasattr(response_headers, "get")
            else "<missing>"
        )
        try:
            raw_body = response.text
        except Exception as exc:  # pragma: no cover - defensive provider boundary
            raw_body = f"<unavailable: {type(exc).__name__}>"
        raw_body_type = type(raw_body).__name__
        if not isinstance(raw_body, str):
            raw_body = str(raw_body)
        raw_body_length = len(raw_body)
        raw_body_preview = raw_body[:2000]
        api_key = str(self.config.api_key)
        if api_key:
            raw_body_preview = raw_body_preview.replace(api_key, "[REDACTED]")
        printable_preview = raw_body_preview.replace("\r", "\\r").replace(
            "\n", "\\n"
        )
        has_image = any(
            isinstance(part, dict) and part.get("type") in {"image", "image_url"}
            for message in payload["messages"]
            if isinstance(message, dict) and isinstance(message.get("content"), list)
            for part in message["content"]
        )
        request_stream = payload.get("stream", "<omitted>")
        if isinstance(request_stream, bool):
            request_stream = str(request_stream).lower()
        print(f"CHAT_COMPLETIONS_REQUEST_URL={self.endpoint}", file=sys.stderr)
        print(
            f"CHAT_COMPLETIONS_REQUEST_MODEL={self.config.model}",
            file=sys.stderr,
        )
        print(
            f"CHAT_COMPLETIONS_REQUEST_STREAM={request_stream}",
            file=sys.stderr,
        )
        print(
            f"CHAT_COMPLETIONS_HAS_IMAGE={str(has_image).lower()}",
            file=sys.stderr,
        )
        print(f"CHAT_COMPLETIONS_HTTP_STATUS={status_code}", file=sys.stderr)
        print(f"CHAT_COMPLETIONS_CONTENT_TYPE={content_type}", file=sys.stderr)
        print(
            f"CHAT_COMPLETIONS_CONTENT_ENCODING={content_encoding}",
            file=sys.stderr,
        )
        print(
            f"CHAT_COMPLETIONS_TRANSFER_ENCODING={transfer_encoding}",
            file=sys.stderr,
        )
        print(
            f"CHAT_COMPLETIONS_RAW_BODY_TYPE={raw_body_type}",
            file=sys.stderr,
        )
        print(
            f"CHAT_COMPLETIONS_RAW_BODY_LENGTH={raw_body_length}",
            file=sys.stderr,
        )
        print(f"CHAT_COMPLETIONS_RAW_BODY_PREVIEW={printable_preview}", file=sys.stderr)

        if type(status_code) is not int or not 200 <= status_code < 300:
            status = status_code if type(status_code) is int else "unknown"
            raise RegionMaskClientError(
                f"Chat Completions returned HTTP {status}"
            )
        try:
            provider_response = response.json()
        except Exception as exc:
            json_error = f"{type(exc).__name__}: {exc}"
            if api_key:
                json_error = json_error.replace(api_key, "[REDACTED]")
            raise VLMResponseParseError(
                "Chat Completions response body is not valid JSON; "
                f"status_code={status_code}; content_type={content_type}; "
                f"body_length={raw_body_length}; json_decode_error={json_error}; "
                f"body_preview={printable_preview}"
            ) from exc
        try:
            message = provider_response["choices"][0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VLMResponseParseError(
                "Chat Completions response has no assistant content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise VLMResponseParseError(
                "Chat Completions assistant content is empty"
            )
        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            raise VLMResponseParseError(
                "Chat Completions assistant content is not valid JSON"
            ) from exc
        if not isinstance(result, dict):
            raise VLMResponseParseError(
                "Chat Completions assistant content must be a JSON object"
            )
        return result


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AnalysisBBox(_StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class CanonicalText(_StrictModel):
    text: str = Field(min_length=1)
    bbox_analysis: AnalysisBBox
    ownership: Literal["ui_owned", "asset_owned"]
    semantic_role: SemanticRole
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_ownership_role_pair(self) -> "CanonicalText":
        allowed = UI_ROLES if self.ownership == "ui_owned" else ASSET_ROLES
        if self.semantic_role not in allowed:
            raise ValueError(
                f"semantic_role {self.semantic_role!r} is incompatible with "
                f"ownership {self.ownership!r}"
            )
        return self


class CanonicalTextResponse(_StrictModel):
    texts: list[CanonicalText]


def _strict_response_schema() -> dict[str, Any]:
    """Return the Pydantic schema with all object keys explicitly required."""

    schema = copy.deepcopy(CanonicalTextResponse.model_json_schema())

    def make_strict(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                properties = node.get("properties", {})
                node["additionalProperties"] = False
                node["required"] = list(properties)
            node.pop("default", None)
            for value in node.values():
                make_strict(value)
        elif isinstance(node, list):
            for value in node:
                make_strict(value)

    make_strict(schema)
    return schema


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegionMaskInputError(f"File does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegionMaskInputError(f"Cannot read valid JSON from {path}: {exc}") from exc


def _read_stage_a(path: Path) -> tuple[dict[str, Any] | None, list[TextItem]]:
    document = _read_json(path)
    if isinstance(document, list):
        envelope = None
        raw_items = document
    elif isinstance(document, dict) and isinstance(document.get("items"), list):
        envelope = document
        raw_items = document["items"]
    else:
        raise RegionMaskInputError(
            f"Stage A JSON must be a list or an object containing 'items': {path}"
        )
    try:
        items = [TextItem.model_validate(item) for item in raw_items]
    except (ValidationError, TypeError) as exc:
        raise RegionMaskInputError(f"Invalid Stage A text item in {path}: {exc}") from exc
    if len({item.id for item in items}) != len(items):
        raise RegionMaskInputError(f"Stage A text IDs must be unique: {path}")
    if envelope is not None:
        declared_count = envelope.get("count")
        if declared_count is not None and declared_count != len(items):
            raise RegionMaskInputError("Stage A count does not equal len(items)")
    return envelope, items


def _load_bgr_image(path: Path) -> np.ndarray:
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise RegionMaskInputError(f"Unsupported image extension: {path.suffix}")
    try:
        payload = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(payload, cv2.IMREAD_COLOR)
    except OSError as exc:
        raise RegionMaskInputError(f"Cannot read image {path}: {exc}") from exc
    if image is None:
        raise RegionMaskInputError(f"Cannot decode image: {path}")
    return image


def _write_png(path: Path, image: np.ndarray) -> None:
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise RegionMaskInputError(f"Failed to encode PNG: {path}")
    try:
        encoded.tofile(path)
    except OSError as exc:
        raise RegionMaskInputError(f"Failed to write PNG {path}: {exc}") from exc


def _first_environment_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _create_default_client(model: str) -> VLMClient:
    if VLMClientConfig is None or encode_image_as_data_url is None:
        raise RegionMaskClientError(
            "Repository VLM client is unavailable under game-ui-asset-analyzer/scripts"
        )
    base_url = _first_environment_value(
        "OPENAI_BASE_URL", "BASE_URL", "STAGE2A_VLM_BASE_URL"
    )
    api_key = _first_environment_value(
        "OPENAI_API_KEY", "API_KEY", "STAGE2A_VLM_API_KEY"
    )
    if not base_url or not api_key:
        raise RegionMaskClientError(
            "VLM configuration missing: set OPENAI_BASE_URL and OPENAI_API_KEY"
        )
    timeout_text = _first_environment_value("VLM_TIMEOUT", "STAGE2A_VLM_TIMEOUT")
    try:
        timeout = float(timeout_text) if timeout_text else 60.0
    except ValueError as exc:
        raise RegionMaskClientError(f"Invalid VLM timeout: {timeout_text!r}") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise RegionMaskClientError("VLM timeout must be a positive finite number")
    return ChatCompletionsSchemaVLMClient(
        VLMClientConfig(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
    )


def _validate_source_candidates(
    items: list[TextItem], source_width: int, source_height: int
) -> None:
    for item in items:
        rect = item.rect
        if rect.x + rect.width > source_width or rect.y + rect.height > source_height:
            raise RegionMaskInputError(
                f"Stage A bbox for {item.id!r} exceeds the source image"
            )


def _validate_analysis_metadata(
    analysis_image_path: Path,
    metadata: Any,
    source_width: int,
    source_height: int,
) -> tuple[int, int]:
    if not isinstance(metadata, dict):
        raise RegionMaskInputError("Analysis-image metadata must be an object")
    source_size = metadata.get("source_size")
    analysis_size = metadata.get("analysis_size")
    if not isinstance(source_size, dict) or not isinstance(analysis_size, dict):
        raise RegionMaskInputError("Analysis-image metadata has no size records")
    try:
        metadata_source = (int(source_size["width"]), int(source_size["height"]))
        analysis_width = int(analysis_size["width"])
        analysis_height = int(analysis_size["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RegionMaskInputError("Analysis-image metadata sizes are invalid") from exc
    if metadata_source != (source_width, source_height):
        raise RegionMaskInputError("Analysis metadata does not match the source image")
    if analysis_width <= 0 or analysis_height <= 0:
        raise RegionMaskInputError("Analysis image dimensions must be positive")
    prepared = _load_bgr_image(analysis_image_path)
    if prepared.shape[:2] != (analysis_height, analysis_width):
        raise RegionMaskInputError(
            "Prepared analysis image dimensions do not match its metadata"
        )
    return analysis_width, analysis_height


def _source_to_analysis_bbox(
    item: TextItem,
    *,
    source_width: int,
    source_height: int,
    analysis_width: int,
    analysis_height: int,
) -> dict[str, int]:
    rect = item.rect
    scale_x = analysis_width / source_width
    scale_y = analysis_height / source_height
    x0 = max(0, min(analysis_width - 1, math.floor(rect.x * scale_x)))
    y0 = max(0, min(analysis_height - 1, math.floor(rect.y * scale_y)))
    x1 = max(x0 + 1, min(analysis_width, math.ceil((rect.x + rect.width) * scale_x)))
    y1 = max(y0 + 1, min(analysis_height, math.ceil((rect.y + rect.height) * scale_y)))
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _ocr_hints(
    items: list[TextItem],
    *,
    source_width: int,
    source_height: int,
    analysis_width: int,
    analysis_height: int,
) -> list[dict[str, Any]]:
    return [
        {
            "text": item.text,
            "bbox_analysis": _source_to_analysis_bbox(
                item,
                source_width=source_width,
                source_height=source_height,
                analysis_width=analysis_width,
                analysis_height=analysis_height,
            ),
            "ocr_confidence": item.confidence,
        }
        for item in items
    ]


def _build_user_prompt(
    hints: list[dict[str, Any]], analysis_width: int, analysis_height: int
) -> str:
    return (
        f"Analysis image size: {analysis_width}x{analysis_height} pixels.\n"
        "The following Stage A OCR candidates are non-authoritative hints. "
        "Their boxes are already mapped to analysis-image pixel coordinates. "
        "Do not copy a hint unless the screenshot supports it, and do not return OCR IDs.\n"
        "OCR candidates:\n"
        f"{json.dumps(hints, ensure_ascii=False, separators=(',', ':'))}\n"
        "Return the complete canonical text list for the screenshot. All bbox_analysis "
        "values must be integer pixel rectangles fully inside the analysis image."
    )


def _validate_canonical_response(
    payload: Any, analysis_width: int, analysis_height: int
) -> CanonicalTextResponse:
    """Decode and strictly validate one response without accepting aliases."""

    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
        response = CanonicalTextResponse.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise RegionMaskResponseError(f"Invalid VLM response: {exc}") from exc
    _validate_response_bounds(response, analysis_width, analysis_height)
    return response


def _infer_canonical_response_with_schema_retry(
    client: VLMClient,
    *,
    image_path: Path,
    user_prompt: str,
    response_schema: dict[str, Any],
    analysis_width: int,
    analysis_height: int,
) -> CanonicalTextResponse:
    """Retry exactly once when model JSON violates the canonical schema."""

    last_error: RegionMaskResponseError | None = None
    for attempt in range(2):
        attempt_prompt = user_prompt
        if attempt == 1:
            attempt_prompt = f"{user_prompt}\n\n{SCHEMA_RETRY_INSTRUCTION}"
        try:
            payload = client.infer_json(
                image_path=image_path,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=attempt_prompt,
                response_schema=response_schema,
            )
        except (VLMResponseParseError, json.JSONDecodeError) as exc:
            last_error = RegionMaskResponseError(
                f"Invalid VLM response: response is not valid JSON: {exc}"
            )
        else:
            try:
                return _validate_canonical_response(
                    payload, analysis_width, analysis_height
                )
            except RegionMaskResponseError as exc:
                last_error = exc

    if last_error is None:  # pragma: no cover - loop always records an error or returns
        raise AssertionError("schema retry loop exhausted without a result")
    raise RegionMaskResponseError(
        f"VLM response failed schema validation after 2 attempts: {last_error}"
    ) from last_error


def _analysis_to_source_bbox(
    bbox: AnalysisBBox,
    *,
    analysis_width: int,
    analysis_height: int,
    source_width: int,
    source_height: int,
) -> dict[str, int]:
    """Map a validated VLM box using floor/ceil edges, then clamp."""

    scale_x = source_width / analysis_width
    scale_y = source_height / analysis_height
    x0 = max(0, min(source_width - 1, math.floor(bbox.x * scale_x)))
    y0 = max(0, min(source_height - 1, math.floor(bbox.y * scale_y)))
    x1 = max(
        x0 + 1,
        min(source_width, math.ceil((bbox.x + bbox.width) * scale_x)),
    )
    y1 = max(
        y0 + 1,
        min(source_height, math.ceil((bbox.y + bbox.height) * scale_y)),
    )
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _validate_response_bounds(
    response: CanonicalTextResponse, analysis_width: int, analysis_height: int
) -> None:
    for index, item in enumerate(response.texts):
        bbox = item.bbox_analysis
        if bbox.x + bbox.width > analysis_width or bbox.y + bbox.height > analysis_height:
            raise RegionMaskResponseError(
                f"Invalid VLM response: texts[{index}].bbox_analysis exceeds "
                f"the {analysis_width}x{analysis_height} analysis image"
            )


def _mask_bbox(
    mask: np.ndarray, bbox_source: dict[str, int], padding_px: int
) -> None:
    image_height, image_width = mask.shape
    x0 = max(0, bbox_source["x"] - padding_px)
    y0 = max(0, bbox_source["y"] - padding_px)
    x1 = min(
        image_width,
        bbox_source["x"] + bbox_source["width"] + padding_px,
    )
    y1 = min(
        image_height,
        bbox_source["y"] + bbox_source["height"] + padding_px,
    )
    mask[y0:y1, x0:x1] = 255


def _build_overlay(original_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    red_layer = np.zeros_like(original_bgr)
    red_layer[:, :] = (0, 0, 255)
    blended = cv2.addWeighted(original_bgr, 0.55, red_layer, 0.45, 0.0)
    overlay = original_bgr.copy()
    overlay[mask > 0] = blended[mask > 0]
    return overlay


class UIVLMRegionMaskPoC:
    """Use OCR only as a hint and VLM rectangles as the sole mask authority."""

    def __init__(
        self,
        client: VLMClient | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.client = client
        self.model = model

    def process(
        self,
        image_path: Path | str,
        texts_json_path: Path | str,
        output_dir: Path | str,
        *,
        padding_px: int = 0,
    ) -> dict[str, Any]:
        if type(padding_px) is not int or padding_px < 0:
            raise RegionMaskInputError("padding_px must be a non-negative integer")

        source_path = Path(image_path)
        texts_path = Path(texts_json_path)
        destination = Path(output_dir)
        original = _load_bgr_image(source_path)
        source_height, source_width = original.shape[:2]
        envelope, candidates = _read_stage_a(texts_path)
        if envelope is not None:
            declared_width = envelope.get("image_width")
            declared_height = envelope.get("image_height")
            if declared_width not in (None, source_width) or declared_height not in (
                None,
                source_height,
            ):
                raise RegionMaskInputError(
                    "Stage A image dimensions do not match the supplied screenshot"
                )
        _validate_source_candidates(candidates, source_width, source_height)

        if prepare_analysis_input is None:
            raise RegionMaskClientError(
                "Repository image preparation helper is unavailable"
            )
        schema = _strict_response_schema()
        client = self.client or _create_default_client(self.model)
        try:
            with tempfile.TemporaryDirectory(prefix="ui-vlm-region-mask-") as temp_dir:
                analysis_image = Path(temp_dir) / "analysis.png"
                metadata_path = Path(temp_dir) / "analysis.metadata.json"
                metadata = prepare_analysis_input(
                    source_path,
                    analysis_image,
                    metadata_path,
                    max_width=DEFAULT_MAX_IMAGE_WIDTH,
                    force_width=True,
                )
                analysis_width, analysis_height = _validate_analysis_metadata(
                    analysis_image,
                    metadata,
                    source_width,
                    source_height,
                )
                hints = _ocr_hints(
                    candidates,
                    source_width=source_width,
                    source_height=source_height,
                    analysis_width=analysis_width,
                    analysis_height=analysis_height,
                )
                response = _infer_canonical_response_with_schema_retry(
                    client,
                    image_path=analysis_image,
                    user_prompt=_build_user_prompt(
                        hints, analysis_width, analysis_height
                    ),
                    response_schema=schema,
                    analysis_width=analysis_width,
                    analysis_height=analysis_height,
                )
        except RegionMaskError:
            raise
        except Exception as exc:
            raise RegionMaskClientError(f"VLM region analysis failed: {exc}") from exc

        mask = np.zeros((source_height, source_width), dtype=np.uint8)
        plan_texts: list[dict[str, Any]] = []
        for item in response.texts:
            bbox_source = _analysis_to_source_bbox(
                item.bbox_analysis,
                analysis_width=analysis_width,
                analysis_height=analysis_height,
                source_width=source_width,
                source_height=source_height,
            )
            decision = (
                "remove_for_background_repair"
                if item.ownership == "ui_owned"
                else "preserve_as_visual_asset"
            )
            if item.ownership == "ui_owned":
                _mask_bbox(mask, bbox_source, padding_px)
            plan_texts.append(
                {
                    "text": item.text,
                    "bbox_analysis": item.bbox_analysis.model_dump(),
                    "bbox_source": bbox_source,
                    "ownership": item.ownership,
                    "semantic_role": item.semantic_role,
                    "decision": decision,
                    "confidence": item.confidence,
                }
            )

        plan = {
            "schema_version": SCHEMA_VERSION,
            "source_image_size": {"width": source_width, "height": source_height},
            "analysis_image_size": {
                "width": analysis_width,
                "height": analysis_height,
            },
            "padding_px": padding_px,
            "texts": plan_texts,
        }
        overlay = _build_overlay(original, mask)

        try:
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "vlm-region-plan.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _write_png(destination / "region-mask.png", mask)
            _write_png(destination / "region-mask-overlay.png", overlay)
        except (OSError, UnicodeError) as exc:
            raise RegionMaskInputError(f"Cannot write outputs to {destination}: {exc}") from exc
        return plan


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a UI-owned rectangular region mask from one canonical VLM text pass."
        )
    )
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--texts-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--padding-px",
        type=int,
        default=0,
        help="optional source-image pixel padding around UI-owned boxes (default: 0)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        plan = UIVLMRegionMaskPoC(model=args.model).process(
            args.image,
            args.texts_json,
            args.output_dir,
            padding_px=args.padding_px,
        )
    except (RegionMaskError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    ui_count = sum(item["ownership"] == "ui_owned" for item in plan["texts"])
    asset_count = len(plan["texts"]) - ui_count
    print(
        f"Region mask complete: {ui_count} UI-owned and "
        f"{asset_count} asset-owned text regions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
