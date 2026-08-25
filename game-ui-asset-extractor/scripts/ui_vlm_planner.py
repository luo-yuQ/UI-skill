#!/usr/bin/env python3
"""Stage 2.2.1 dual-image VLM planner for semantic UI asset layers."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageOps
from pydantic import ValidationError

from ui_plan_models import LayerPlanResult

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - production-only dependency guard
    requests = None  # type: ignore[assignment]


DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_OUTPUT_TOKENS = 4000
TEMPERATURE = 0.1
DUAL_IMAGE_MAX_WIDTH = 1024
DUAL_IMAGE_WEBP_QUALITY = 85
COMPOSITE_VIEW_MAX_WIDTH = 1280
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


SYSTEM_PROMPT = """You are a senior game UI asset decomposition and segmentation planner.

INPUTS
You receive exactly two views of the same UI at the same resolution, in this order:
1. the original high-resolution UI image, which is authoritative for text appearance and
   original visual semantics;
2. cleaned_image, the OCR-cleaned working image, which is authoritative for inspecting
   material boundaries after ordinary editable text has been removed.
You also receive a compact Stage 0 OCR list containing only id, text, and rect.

TEXT RENDERING DECISION
- Keep readable UI copy (labels, prices, counters, descriptions, levels, and other dynamic
  copy) as editable text. Do not create asset queries for it and do not list its OCR ID in
  raster_text_ids.
- Put an OCR ID in raster_text_ids only when the text is inseparable raster artwork: an
  irregular hand-drawn wordmark, multicolor-filled lettering, embossed/outlined display
  mark, game logo, team mark, or comparable illustrated lettering.
- Reference only OCR IDs present in the supplied Stage 0 list.

ASSETS TO EXTRACT
- large panels and ornamental frames;
- every repeated card or slot as a separate instance;
- complete button artwork, excluding its editable text label;
- independent icons and illustrations;
- badges, ribbons, tabs, status marks, and meaningful decorations.

KIND AND ROLE MAPPING
- kind="panel" covers a large panel, base, or ornamental frame; "card" and "slot"
  identify those repeated component types; "button" is complete clickable button artwork;
  "icon" covers a compact standalone icon/illustration; "badge" covers a badge, ribbon,
  tab, or status marker; "logo" is an inseparable graphical mark; and "decoration" covers
  a larger independent illustration or ornament not represented by another allowed kind.
- role="container" supports child materials; "interactive" is a control; "visual_artwork"
  is self-contained art; and "foreground" is a non-interactive overlay above other layers.
- Use a stable, descriptive, unique snake_case id for each query.

STRICT EXCLUSIONS
Never output the full-screen background as a query. Never output ordinary text, cast/drop
shadows, tiny seams, divider lines, speculative objects that cannot be confirmed visually,
or sub-objects such as a face or weapon inside an already framed illustration/card artwork.

GEOMETRY HINTS FOR SAM
- Each query is one independently named instance. Never combine repeated instances into a
  single query or a single bounding box.
- bbox_norm is [x0, y0, x1, y1], tightly enclosing only that instance, with every value in
  [0, 1]. Avoid loose container-sized boxes for smaller children.
- Give 1-3 positive_points_norm anchors on unmistakable opaque/entity pixels of the target,
  distributed across it rather than on text, transparent holes, or the background.
- Give 0-2 negative_points_norm anchors on nearby competing objects when they help separate
  touching/overlapping assets. Negative points must not lie on the target.

LAYER TOPOLOGY
- Return queries in back-to-front order with strictly increasing z_order.
- parent_query_id is the nearest direct material parent that contains/supports the query,
  not merely a semantic grouping. Root material layers use null.
- Every parent's z_order must be strictly lower than each direct child's z_order.

REPAIR MODES
- element_repair_mode="image" when pixels hidden by an extracted child belong to complex
  illustration, patterned material, or irregular texture and require image-aware repair.
- element_repair_mode="surface" for flat colors, smooth gradients, and simple repeatable
  surfaces.
- element_repair_mode="none" for a topmost layer with no removed child exposing missing
  pixels, or when no element-level repair is needed.
- background_repair.mode="scene" for a complex pictorial background, "surface" for a flat
  or smoothly varying UI surface, and "none" when no background repair is needed. Describe
  the intended repair briefly or use null when mode is "none".

OUTPUT CONTRACT
Return exactly one pure JSON object matching the supplied LayerPlanResult JSON Schema.
Do not use Markdown fences, comments, prose outside JSON, extra fields, invented IDs, or
non-normalized coordinates.
"""

COMPOSITE_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    """You receive exactly two views of the same UI at the same resolution, in this order:
1. the original high-resolution UI image, which is authoritative for text appearance and
   original visual semantics;
2. cleaned_image, the OCR-cleaned working image, which is authoritative for inspecting
   material boundaries after ordinary editable text has been removed.
You also receive a compact Stage 0 OCR list containing only id, text, and rect.""",
    """You receive one comparison sheet containing two views of the same UI:
1. LEFT, labelled IMAGE 1 - ORIGINAL: the original UI, authoritative for text appearance
   and original visual semantics;
2. RIGHT, labelled IMAGE 2 - CLEANED: cleaned_image, authoritative for inspecting material
   boundaries after ordinary editable text has been removed.
The two views preserve the same scale and are ordered left-to-right. You also receive a
compact Stage 0 OCR list containing only id, text, and rect.""",
)


class VLMPlannerError(RuntimeError):
    """Base error for the Stage 2.2.1 planning pipeline."""


class PlannerInputError(VLMPlannerError):
    """Raised when local images or Stage 0 text data are invalid."""


class PlannerClientError(VLMPlannerError):
    """Raised when the configured VLM cannot complete a request."""


class PlannerResponseError(VLMPlannerError):
    """Raised when a VLM response violates the planning contract."""


class VLMClient(Protocol):
    """Narrow injectable boundary used by the layer planner and its tests."""

    def infer_json(
        self,
        original_image_path: Path,
        cleaned_image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> Any: ...


def _strict_response_schema() -> dict[str, Any]:
    """Build a relay-friendly strict schema from the Pydantic contract."""

    schema = copy.deepcopy(LayerPlanResult.model_json_schema())

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


def _read_text_summary(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    """Load Stage 0 output and retain only ``id``, ``text``, and ``rect``."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PlannerInputError(f"File does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PlannerInputError(f"Cannot read valid JSON from {path}: {exc}") from exc

    if isinstance(document, list):
        items = document
    elif isinstance(document, dict) and isinstance(document.get("items"), list):
        items = document["items"]
    else:
        raise PlannerInputError(
            f"texts JSON must be a list or an object containing 'items': {path}"
        )

    summary: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise PlannerInputError(f"Text item at index {index} must be an object")
        text_id = item.get("id")
        text = item.get("text")
        rect = item.get("rect")
        if not isinstance(text_id, str) or not text_id:
            raise PlannerInputError(f"Text item at index {index} has no valid id")
        if text_id in seen_ids:
            raise PlannerInputError(f"Duplicate text ID: {text_id}")
        if not isinstance(text, str):
            raise PlannerInputError(f"Text item {text_id!r} has no valid text")
        if not isinstance(rect, (dict, list)):
            raise PlannerInputError(f"Text item {text_id!r} has no valid rect")
        summary.append({"id": text_id, "text": text, "rect": rect})
        seen_ids.add(text_id)
    return summary, seen_ids


def _inspect_image(path: Path) -> tuple[int, int]:
    """Verify a supported image and return its dimensions."""

    if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise PlannerInputError(f"Unsupported image extension: {path.suffix}")
    try:
        with Image.open(path) as image:
            image.load()
            return image.size
    except FileNotFoundError as exc:
        raise PlannerInputError(f"File does not exist: {path}") from exc
    except OSError as exc:
        raise PlannerInputError(f"Cannot decode image {path}: {exc}") from exc


def _encode_image_as_data_url(path: Path) -> str:
    """Downscale one input to the relay-tested 1024px WebP data URL."""

    if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise PlannerInputError(f"Unsupported image extension: {path.suffix}")
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if image.width > DUAL_IMAGE_MAX_WIDTH:
                target_height = max(
                    1,
                    round(image.height * DUAL_IMAGE_MAX_WIDTH / image.width),
                )
                image = image.resize(
                    (DUAL_IMAGE_MAX_WIDTH, target_height),
                    Image.Resampling.LANCZOS,
                )
            buffer = BytesIO()
            image.save(
                buffer,
                format="WEBP",
                quality=DUAL_IMAGE_WEBP_QUALITY,
                method=6,
            )
    except (FileNotFoundError, OSError) as exc:
        raise PlannerInputError(f"Cannot preprocess image {path}: {exc}") from exc
    encoded = b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


def _encode_comparison_as_data_url(
    original_image_path: Path,
    cleaned_image_path: Path,
) -> str:
    """Build a compact one-image comparison sheet for single-image relays."""

    try:
        with Image.open(original_image_path) as original_source:
            original = original_source.convert("RGB")
        with Image.open(cleaned_image_path) as cleaned_source:
            cleaned = cleaned_source.convert("RGB")
    except (FileNotFoundError, OSError) as exc:
        raise PlannerInputError(f"Cannot build comparison image: {exc}") from exc

    if original.size != cleaned.size:
        raise PlannerInputError(
            "Original and cleaned images must have identical dimensions"
        )
    source_width, source_height = original.size
    scale = min(1.0, COMPOSITE_VIEW_MAX_WIDTH / source_width)
    view_size = (
        max(1, round(source_width * scale)),
        max(1, round(source_height * scale)),
    )
    if original.size != view_size:
        original = original.resize(view_size, Image.Resampling.LANCZOS)
        cleaned = cleaned.resize(view_size, Image.Resampling.LANCZOS)

    label_height = 40
    sheet = Image.new(
        "RGB",
        (view_size[0] * 2, view_size[1] + label_height),
        (20, 24, 32),
    )
    sheet.paste(original, (0, label_height))
    sheet.paste(cleaned, (view_size[0], label_height))
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 12), "IMAGE 1 - ORIGINAL", fill=(255, 255, 255))
    draw.text(
        (view_size[0] + 12, 12),
        "IMAGE 2 - CLEANED",
        fill=(255, 255, 255),
    )
    draw.line(
        (view_size[0], 0, view_size[0], sheet.height - 1),
        fill=(255, 196, 64),
        width=3,
    )
    buffer = BytesIO()
    sheet.save(buffer, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + b64encode(buffer.getvalue()).decode("ascii")


def _responses_endpoint(base_url: str) -> str:
    """Accept root, ``/v1``, or full Responses relay base URLs."""

    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PlannerClientError("BASE_URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise PlannerClientError("BASE_URL must not contain a query or fragment")
    if parsed.path.endswith("/v1/responses"):
        return normalized
    if parsed.path.endswith("/v1"):
        return normalized + "/responses"
    return normalized + "/v1/responses"


def _extract_response_text(response: Any) -> str:
    """Extract assistant text from Responses API or a compatible relay body."""

    if isinstance(response, dict) and isinstance(response.get("output_text"), str):
        return response["output_text"]
    output = response.get("output") if isinstance(response, dict) else None
    if isinstance(output, list):
        for message in output:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    return part["text"]
    choices = response.get("choices") if isinstance(response, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
    raise PlannerResponseError("VLM response contains no assistant output text")


_UNESCAPED_OUTPUT_TEXT_PATTERN = re.compile(
    r'"type"\s*:\s*"output_text"\s*,\s*"text"\s*:\s*"'
)


def _extract_unescaped_relay_output_text(response_body: str) -> str:
    """Recover JSON inserted unescaped into a relay's ``text`` string field.

    Some Responses-compatible relays serialize an otherwise successful response as
    ``"text":"{"key":...}"`` instead of escaping the inner JSON quotes. The full
    envelope is invalid JSON, but the model's object remains a valid JSON prefix.
    This fallback deliberately accepts only that narrow, recognizable shape.
    """

    decoder = json.JSONDecoder()
    for match in _UNESCAPED_OUTPUT_TEXT_PATTERN.finditer(response_body):
        candidate = response_body[match.end() :].lstrip()
        if not candidate.startswith("{"):
            continue
        try:
            value, end_index = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        trailer = candidate[end_index:].lstrip()
        if isinstance(value, dict) and trailer.startswith('"'):
            return candidate[:end_index]
    raise PlannerResponseError(
        "VLM response body is invalid JSON and contains no recoverable output_text object"
    )


class OpenAICompatibleVLMClient:
    """Minimal dual-image client for OpenAI Responses-compatible relays."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        image_mode: Literal["dual", "composite"] = "dual",
        session: Any | None = None,
    ) -> None:
        if timeout <= 0:
            raise PlannerClientError("VLM timeout must be positive")
        if not api_key:
            raise PlannerClientError("API_KEY must not be empty")
        if image_mode not in {"dual", "composite"}:
            raise PlannerClientError(f"Unsupported image mode: {image_mode}")
        if session is None:
            if requests is None:
                raise PlannerClientError("The requests package is required for VLM calls")
            session = requests.Session()
        self.endpoint = _responses_endpoint(base_url)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.image_mode = image_mode
        self.session = session

    def infer_json(
        self,
        original_image_path: Path,
        cleaned_image_path: Path,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send original then cleaned image, and parse one strict JSON object."""

        schema_instruction = ""
        if response_schema is not None:
            schema_instruction = (
                "\n\nRequired LayerPlanResult JSON Schema:\n"
                + json.dumps(response_schema, ensure_ascii=False, separators=(",", ":"))
            )
        input_text = user_prompt + schema_instruction
        content: list[dict[str, Any]]
        if self.image_mode == "dual":
            content = [
                {"type": "input_text", "text": input_text},
                {
                    "type": "input_image",
                    "image_url": _encode_image_as_data_url(original_image_path),
                },
                {
                    "type": "input_image",
                    "image_url": _encode_image_as_data_url(cleaned_image_path),
                },
            ]
        else:
            input_text += (
                "\n\nOne comparison sheet follows: original on the LEFT, "
                "cleaned_image on the RIGHT."
            )
            content = [
                {"type": "input_text", "text": input_text},
                {
                    "type": "input_image",
                    "image_url": _encode_comparison_as_data_url(
                        original_image_path,
                        cleaned_image_path,
                    ),
                },
            ]

        payload = {
            "model": self.model,
            "temperature": TEMPERATURE,
            "top_p": 1,
            "instructions": system_prompt,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": content,
                }
            ],
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "UIVLMPlanner/1.0",
        }
        try:
            response = self.session.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            raise PlannerClientError(f"VLM request failed: {type(exc).__name__}") from exc

        raw_response_body = getattr(response, "text", "")
        if not isinstance(raw_response_body, str) or not raw_response_body.strip():
            status_code = getattr(response, "status_code", "unknown")
            response_headers = getattr(response, "headers", {})
            content_type = (
                response_headers.get("Content-Type", "unknown")
                if hasattr(response_headers, "get")
                else "unknown"
            )
            request_bytes = len(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            raise PlannerResponseError(
                "VLM response body is empty "
                f"(HTTP {status_code}, content_type={content_type}, "
                f"request_bytes={request_bytes}, endpoint={self.endpoint})"
            )
        try:
            provider_response = json.loads(raw_response_body)
        except json.JSONDecodeError:
            response_text = _extract_unescaped_relay_output_text(raw_response_body)
        else:
            response_text = _extract_response_text(provider_response)
        response_text = response_text.strip()
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise PlannerResponseError("VLM output is not pure valid JSON") from exc
        if not isinstance(result, dict):
            raise PlannerResponseError("VLM output JSON must be an object")
        return result


def _first_environment_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _create_default_client(
    model: str,
    image_mode: Literal["dual", "composite"] = "dual",
) -> OpenAICompatibleVLMClient:
    """Create a client from relay aliases or standard OpenAI environment names."""

    base_url = _first_environment_value(
        "BASE_URL", "OPENAI_BASE_URL", "STAGE2A_VLM_BASE_URL"
    )
    api_key = _first_environment_value(
        "API_KEY", "OPENAI_API_KEY", "STAGE2A_VLM_API_KEY"
    )
    if not base_url or not api_key:
        raise PlannerClientError(
            "VLM configuration missing: set BASE_URL/API_KEY or "
            "OPENAI_BASE_URL/OPENAI_API_KEY"
        )
    timeout_text = _first_environment_value("VLM_TIMEOUT", "STAGE2A_VLM_TIMEOUT")
    try:
        timeout = float(timeout_text) if timeout_text else DEFAULT_TIMEOUT
    except ValueError as exc:
        raise PlannerClientError(f"Invalid VLM timeout: {timeout_text!r}") from exc
    return OpenAICompatibleVLMClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        image_mode=image_mode,
    )


class UIVLMPlanner:
    """Create and export a semantic layer plan from original and cleaned images."""

    def __init__(
        self,
        client: VLMClient | None = None,
        model: str = DEFAULT_MODEL,
        image_mode: Literal["dual", "composite"] = "dual",
    ) -> None:
        if image_mode not in {"dual", "composite"}:
            raise ValueError(f"Unsupported image mode: {image_mode}")
        self.client = client
        self.model = model
        self.image_mode = image_mode

    def plan(
        self,
        original_image_path: Path | str,
        cleaned_image_path: Path | str,
        texts_json_path: Path | str,
    ) -> LayerPlanResult:
        """Call the VLM once and strictly validate its layer plan."""

        original_path = Path(original_image_path)
        cleaned_path = Path(cleaned_image_path)
        texts_path = Path(texts_json_path)
        original_size = _inspect_image(original_path)
        cleaned_size = _inspect_image(cleaned_path)
        if original_size != cleaned_size:
            raise PlannerInputError(
                "Original and cleaned images must have identical dimensions"
            )
        text_summary, source_text_ids = _read_text_summary(texts_path)
        user_prompt = (
            f"Image dimensions: {original_size[0]}x{original_size[1]} pixels.\n"
            "Stage 0 compact text candidates (pixel rects):\n"
            + json.dumps(text_summary, ensure_ascii=False, indent=2)
            + "\nPlan every confirmed extractable asset instance now."
        )
        client = self.client or _create_default_client(self.model, self.image_mode)
        system_prompt = (
            COMPOSITE_SYSTEM_PROMPT
            if self.image_mode == "composite"
            else SYSTEM_PROMPT
        )
        try:
            response = client.infer_json(
                original_path,
                cleaned_path,
                system_prompt,
                user_prompt,
                _strict_response_schema(),
            )
        except VLMPlannerError:
            raise
        except Exception as exc:
            raise PlannerClientError(f"VLM request failed: {exc}") from exc

        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError as exc:
                raise PlannerResponseError("VLM output is not pure valid JSON") from exc
        try:
            result = LayerPlanResult.model_validate(response)
        except ValidationError as exc:
            raise PlannerResponseError(f"Invalid LayerPlanResult: {exc}") from exc

        unknown_text_ids = set(result.raster_text_ids) - source_text_ids
        if unknown_text_ids:
            raise PlannerResponseError(
                "VLM response references unknown raster text IDs: "
                f"{sorted(unknown_text_ids)}"
            )
        return result

    def export_artifacts(
        self,
        result: LayerPlanResult,
        cleaned_image_path: Path | str,
        output_json_path: Path | str,
        output_vis_path: Path | str,
    ) -> None:
        """Write formatted ``layer_plan.json`` and a SAM-prompt debug image."""

        cleaned_path = Path(cleaned_image_path)
        output_json = Path(output_json_path)
        output_vis = Path(output_vis_path)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_vis.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

        try:
            with Image.open(cleaned_path) as source:
                debug_image = source.convert("RGB")
        except (FileNotFoundError, OSError) as exc:
            raise PlannerInputError(f"Cannot decode image {cleaned_path}: {exc}") from exc
        draw = ImageDraw.Draw(debug_image)
        width, height = debug_image.size
        palette = (
            "#00E5FF",
            "#FFD166",
            "#B388FF",
            "#FF7A90",
            "#78E08F",
            "#FF9F43",
        )

        def point_xy(point: list[float]) -> tuple[int, int]:
            return (
                min(width - 1, max(0, round(point[0] * (width - 1)))),
                min(height - 1, max(0, round(point[1] * (height - 1)))),
            )

        for query_index, query in enumerate(result.queries):
            color = palette[query_index % len(palette)]
            for hint in query.geometry_hints:
                x0, y0 = point_xy(hint.bbox_norm[:2])
                x1, y1 = point_xy(hint.bbox_norm[2:])
                draw.rectangle((x0, y0, x1, y1), outline=color, width=3)
                label = f"{query.z_order}:{query.id}"
                label_y = max(0, y0 - 13)
                try:
                    draw.text((x0 + 2, label_y), label, fill=color, stroke_width=2,
                              stroke_fill="#000000")
                except UnicodeEncodeError:  # pragma: no cover - old Pillow fallback
                    safe_label = label.encode("ascii", "replace").decode("ascii")
                    draw.text((x0 + 2, label_y), safe_label, fill=color)
                for point in hint.positive_points_norm:
                    x, y = point_xy(point)
                    radius = 5
                    draw.ellipse(
                        (x - radius, y - radius, x + radius, y + radius),
                        fill="#20E070",
                        outline="#062B14",
                        width=2,
                    )
                for point in hint.negative_points_norm:
                    x, y = point_xy(point)
                    radius = 6
                    draw.line(
                        (x - radius, y - radius, x + radius, y + radius),
                        fill="#FF304F",
                        width=3,
                    )
                    draw.line(
                        (x - radius, y + radius, x + radius, y - radius),
                        fill="#FF304F",
                        width=3,
                    )
        try:
            debug_image.save(output_vis, format="PNG")
        except OSError as exc:
            raise PlannerInputError(f"Cannot write debug image {output_vis}: {exc}") from exc

    def process(
        self,
        original_image_path: Path | str,
        cleaned_image_path: Path | str,
        texts_json_path: Path | str,
        output_json_path: Path | str,
        output_vis_path: Path | str,
    ) -> LayerPlanResult:
        """Plan and export both required Stage 2.2.1 artifacts."""

        result = self.plan(original_image_path, cleaned_image_path, texts_json_path)
        self.export_artifacts(
            result,
            cleaned_image_path,
            output_json_path,
            output_vis_path,
        )
        return result


def build_argument_parser() -> argparse.ArgumentParser:
    """Create the Stage 2.2.1 CLI parser."""

    parser = argparse.ArgumentParser(
        description="Plan semantic game UI asset layers with a dual-image VLM."
    )
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--cleaned", type=Path, required=True)
    parser.add_argument("--texts-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-vis", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--image-mode",
        choices=("dual", "composite"),
        default="dual",
        help="Send two images, or one left/right comparison sheet for limited relays",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    args = build_argument_parser().parse_args(argv)
    try:
        result = UIVLMPlanner(model=args.model, image_mode=args.image_mode).process(
            args.original,
            args.cleaned,
            args.texts_json,
            args.output_json,
            args.output_vis,
        )
    except (VLMPlannerError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Layer planning complete: {len(result.queries)} queries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
