#!/usr/bin/env python3
"""Stage2-A1 dual-image direct asset discovery probe.

Minimal-difference variant of direct_asset_discovery_probe.py that sends two
aligned images in one Chat Completions request:

IMAGE 1 - Original UI. Semantic reference only; never a bbox authority.
IMAGE 2 - Clean UI. Authoritative production image; every candidate and every
bbox is based on IMAGE 2 in Analysis Image pixel coordinates.

The experiment tests whether Original + Clean improves Stage2-A1 Direct Asset
Discovery over Clean alone. The production VLM transport, config, retry,
response_format, schema, and bbox validation are reused unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection

from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_asset_analysis import map_bbox_to_source  # noqa: E402
from prepare_analysis_input import (  # noqa: E402
    DEFAULT_MAX_WIDTH,
    prepare_analysis_input,
)
from resolve_terminal_state import load_frozen_taxonomy  # noqa: E402
from runtime_geometry import read_image_size  # noqa: E402
from vlm_client import (  # noqa: E402
    ChatCompletionsVLMClient,
    VLMClientConfig,
    VLMConfigurationError,
    VLMError,
    VLMResponseParseError,
    VLMResponseTruncatedError,
    VLMTransportError,
    encode_image_as_data_url,
    parse_json_object,
)


SCHEMA_VERSION = "0.1"
DIRECT_ASSET_DISCOVERY_MAX_TOKENS = 12000
TAXONOMY_REFERENCE_PATH = ROOT / "references" / "asset-taxonomy.md"
INPUT_MODE = "original_plus_clean"
SYSTEM_PROMPT = """You are performing a direct visual asset census of a game UI.

Two aligned images are provided in this order:

IMAGE 1 — Original UI.
Use IMAGE 1 only as semantic reference for understanding what visible
regions originally represented, especially where ordinary UI text was
removed from the clean working image.

IMAGE 2 — Clean UI.
IMAGE 2 is the authoritative production image for non-text asset discovery
and localization.

All asset candidates and every bounding box must be based on IMAGE 2.

Do not reconstruct or emit text from IMAGE 1.
Do not use IMAGE 1 geometry to override IMAGE 2.
Return exactly one JSON object.
Do not construct a component hierarchy or infer any extraction workflow."""
OVERLAY_COLORS = (
    "#FF3B30",
    "#34C759",
    "#007AFF",
    "#FF9500",
    "#AF52DE",
    "#00C7BE",
    "#FF2D55",
    "#5856D6",
)
REQUEST_AUDIT_HEADERS = {
    "Authorization": "[REDACTED]",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Stage2A-VLMClient/0.1",
    "Accept-Encoding": "identity",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_response_schema(
    taxonomy: Collection[str],
    analysis_size: tuple[int, int],
) -> dict[str, Any]:
    """Build the experiment response schema from the frozen taxonomy values."""

    width, height = analysis_size
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["analysis_image_size", "assets"],
        "properties": {
            "analysis_image_size": {
                "type": "object",
                "additionalProperties": False,
                "required": ["width", "height"],
                "properties": {
                    "width": {"const": width},
                    "height": {"const": height},
                },
            },
            "assets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "label",
                        "taxonomy",
                        "bbox",
                        "partial",
                        "confidence",
                    ],
                    "properties": {
                        "id": {
                            "type": "string",
                            "pattern": r"^asset_[0-9]{3,}$",
                        },
                        "label": {"type": "string", "minLength": 1},
                        "taxonomy": {
                            "type": "string",
                            "enum": sorted(taxonomy),
                        },
                        "bbox": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["x", "y", "width", "height"],
                            "properties": {
                                "x": {"type": "integer", "minimum": 0},
                                "y": {"type": "integer", "minimum": 0},
                                "width": {"type": "integer", "minimum": 1},
                                "height": {"type": "integer", "minimum": 1},
                            },
                        },
                        "partial": {"type": "boolean"},
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                },
            },
        },
    }


def build_user_prompt(
    taxonomy: Collection[str],
    analysis_size: tuple[int, int],
) -> str:
    width, height = analysis_size
    taxonomy_values = ", ".join(sorted(taxonomy))
    taxonomy_reference = TAXONOMY_REFERENCE_PATH.read_text(encoding="utf-8").strip()
    return f"""Perform a direct visual asset census of IMAGE 2, using IMAGE 1 only
for semantic disambiguation.

Identify every visually meaningful terminal asset that is relatively complete,
has independent visual semantics, and could plausibly be extracted or reused as
an individual visual asset. Completeness is more important than minimal output.

Scan the entire image systematically: top, center, bottom, left, and right. Do not
stop after the most salient objects. Include separately visible repeated assets as
separate instances and never merge visually separate instances into one bbox.

Do not construct a component tree, describe parent groups, or return layout-only
groups. Do not plan cropping, foreground extraction, masks, alpha, repair, inpaint,
ownership transforms, or any later-stage extraction work.

Exclude text glyphs, numerals, dynamic text content, empty regions, arbitrary large
groups, and the whole UI/page. Never emit text itself as an asset. A visually
independent non-text surface may still be an asset when it carries text; identify
the surface rather than the lettering.

Use only these frozen taxonomy values: {taxonomy_values}.

Apply the current frozen taxonomy definitions below without inventing new values:

{taxonomy_reference}

For this experiment, the exclusion of text glyphs and numerals overrides any
general taxonomy note about preserving special text artwork. Do not emit a text
asset. Do not turn taxonomy containment examples into a component hierarchy.

The actual Analysis Image is {width} x {height} pixels. Every bbox must use integer
Analysis Image pixel coordinates with a top-left origin. Never use normalized
coordinates. Each bbox must tightly contain the visible asset without unnecessary
surrounding background and must satisfy x >= 0, y >= 0, width >= 1, height >= 1,
x + width <= {width}, and y + height <= {height}.

Candidate and bbox ownership rules:

- Every bbox must correspond to exactly one visual asset instance.
- Every bbox must tightly follow the visible extent of that asset in IMAGE 2.
- Do not include nearby siblings, unrelated surrounding background, or a
  parent surface merely to make the crop more complete.
- Inspect every repeated instance independently. Do not mechanically copy
  geometry from another repeated instance.
- A parent region and a contained foreground child may both be emitted only
  when both have independent visual semantics and independent production value.
- Do not emit a composite region whose only purpose is grouping other valid
  independently reusable assets.
- Do not emit a transient highlight, glow, selected-state overlay, or similar
  state effect as an independent candidate unless it clearly has reusable
  standalone visual identity.
- When IMAGE 1 and IMAGE 2 differ because text was removed, use IMAGE 1 only
  to understand the semantic role; use IMAGE 2 for the actual candidate
  boundary and bbox.

Return JSON only, with exactly this shape:
{{
  "analysis_image_size": {{"width": {width}, "height": {height}}},
  "assets": [
    {{
      "id": "asset_001",
      "label": "concise descriptive semantic label",
      "taxonomy": "one frozen taxonomy value",
      "bbox": {{"x": 0, "y": 0, "width": 1, "height": 1}},
      "partial": false,
      "confidence": 0.95
    }}
  ]
}}
"""


def _format_validation_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_direct_asset_response(
    result: Any,
    response_schema: dict[str, Any],
    analysis_size: tuple[int, int],
) -> None:
    validator = Draft202012Validator(response_schema)
    schema_errors = sorted(
        validator.iter_errors(result),
        key=lambda error: list(error.absolute_path),
    )
    errors = [
        f"{_format_validation_path(error.absolute_path)}: {error.message}"
        for error in schema_errors
    ]
    if not schema_errors:
        width, height = analysis_size
        seen_ids: set[str] = set()
        for index, asset in enumerate(result["assets"]):
            asset_id = asset["id"]
            if asset_id in seen_ids:
                errors.append(f"$.assets[{index}].id: duplicate id {asset_id!r}")
            seen_ids.add(asset_id)
            if asset["taxonomy"] == "text":
                errors.append(
                    f"$.assets[{index}].taxonomy: text glyphs are excluded"
                )
            bbox = asset["bbox"]
            if bbox["x"] + bbox["width"] > width:
                errors.append(
                    f"$.assets[{index}].bbox: right edge exceeds analysis image"
                )
            if bbox["y"] + bbox["height"] > height:
                errors.append(
                    f"$.assets[{index}].bbox: bottom edge exceeds analysis image"
                )
    if errors:
        raise ValueError("invalid direct asset response:\n- " + "\n- ".join(errors))


def build_direct_assets(
    vlm_result: Any,
    source_size: tuple[int, int],
    analysis_size: tuple[int, int],
    *,
    taxonomy: Collection[str] | None = None,
) -> dict[str, Any]:
    """Validate one VLM census and deterministically add source-space bboxes."""

    frozen_taxonomy = load_frozen_taxonomy() if taxonomy is None else taxonomy
    response_schema = build_response_schema(frozen_taxonomy, analysis_size)
    validate_direct_asset_response(vlm_result, response_schema, analysis_size)
    assets = []
    for asset in vlm_result["assets"]:
        bbox_analysis = dict(asset["bbox"])
        assets.append(
            {
                "id": asset["id"],
                "label": asset["label"],
                "taxonomy": asset["taxonomy"],
                "bbox_analysis": bbox_analysis,
                "bbox_source": map_bbox_to_source(
                    bbox_analysis,
                    analysis_size,
                    source_size,
                ),
                "partial": asset["partial"],
                "confidence": asset["confidence"],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_image": "source.png",
        "source_image_size": {
            "width": source_size[0],
            "height": source_size[1],
        },
        "analysis_image": "analysis-image.png",
        "analysis_image_size": {
            "width": analysis_size[0],
            "height": analysis_size[1],
        },
        "assets": assets,
    }


def _safe_overlay_label(value: str, font: ImageFont.ImageFont) -> str:
    label = " ".join(value.split())[:72]
    try:
        font.getbbox(label)
        return label
    except (AttributeError, UnicodeEncodeError):
        return label.encode("ascii", "backslashreplace").decode("ascii")[:72]


def render_overlay(
    image_path: Path,
    direct_assets: dict[str, Any],
    output_path: Path,
    *,
    bbox_field: str,
) -> None:
    """Draw a minimal review overlay for canonical analysis- or source-space boxes."""

    try:
        with Image.open(image_path) as opened:
            opened.load()
            canvas = opened.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read overlay image {image_path}: {exc}") from exc

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for index, asset in enumerate(direct_assets["assets"]):
        color = OVERLAY_COLORS[index % len(OVERLAY_COLORS)]
        bbox = asset[bbox_field]
        left = bbox["x"]
        top = bbox["y"]
        right = left + bbox["width"] - 1
        bottom = top + bbox["height"] - 1
        draw.rectangle((left, top, right, bottom), outline=color, width=3)

        label = _safe_overlay_label(f"{asset['id']} {asset['label']}", font)
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width = max(1, text_box[2] - text_box[0])
        text_height = max(1, text_box[3] - text_box[1])
        label_width = min(canvas.width, text_width + 6)
        label_height = min(canvas.height, text_height + 4)
        label_x = min(max(0, left), max(0, canvas.width - label_width))
        label_y = max(0, top - label_height - 2)
        if label_y == 0 and top < label_height + 2:
            label_y = min(top + 2, max(0, canvas.height - label_height))
        draw.rectangle(
            (
                label_x,
                label_y,
                label_x + label_width - 1,
                label_y + label_height - 1,
            ),
            fill=color,
        )
        draw.text((label_x + 3, label_y + 2), label, fill="white", font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        canvas.save(output_path, format="PNG", compress_level=9)
    except OSError as exc:
        raise ValueError(f"unable to write overlay {output_path}: {exc}") from exc


def _copy_source_image(source: Path, destination: Path) -> None:
    try:
        with Image.open(source) as opened:
            opened.load()
            copied = opened.convert("RGBA" if "A" in opened.getbands() else "RGB")
            destination.parent.mkdir(parents=True, exist_ok=True)
            copied.save(destination, format="PNG", compress_level=9)
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to copy source image {source}: {exc}") from exc


def validate_runs(runs: int) -> None:
    if runs < 1:
        raise ValueError("--runs must be at least 1")


def _raw_provider_response(client: VLMClient) -> Any | None:
    getter = getattr(client, "get_last_provider_response", None)
    return getter() if callable(getter) else None


class DualImageChatCompletionsVLMClient(ChatCompletionsVLMClient):
    """Chat Completions client that sends one text prompt plus two ordered images.

    Reuses the production Chat Completions transport unchanged (endpoint builder,
    session handling, transport retry, headers, response_format json_schema, and
    response parsing). The only difference is the multimodal user message, which
    carries IMAGE 1 (Original UI) before IMAGE 2 (Clean UI, bbox authority).
    """

    def get_last_provider_request(self) -> dict[str, Any] | None:
        """Return the last outgoing payload for secret-free request auditing."""

        return getattr(self._response_local, "provider_request", None)

    def infer_json(
        self,
        original_image_path: Path,
        clean_image_path: Path,
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
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": encode_image_as_data_url(original_image_path)
                            },
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": encode_image_as_data_url(clean_image_path)
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
        if self.config.thinking_policy == "disabled":
            payload["thinking"] = {"type": "disabled"}
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Stage2A-VLMClient/0.1",
            "Accept-Encoding": "identity",
        }
        self._response_local.provider_request = payload
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


def _redact_data_urls(value: Any) -> Any:
    """Replace inline image data URLs with auditable, secret-free markers."""

    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value:
            media_type = value.split(";", 1)[0][len("data:") :]
            encoded = value.split(";base64,", 1)[1]
            return f"[redacted {media_type} data-url; base64_chars={len(encoded)}]"
        return value
    if isinstance(value, list):
        return [_redact_data_urls(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_data_urls(item) for key, item in value.items()}
    return value


def _request_audit(client: DualImageChatCompletionsVLMClient) -> dict[str, Any] | None:
    """Build a secret-free request audit that shows text/image item order."""

    payload = client.get_last_provider_request()
    if payload is None:
        return None
    return {
        "endpoint": client.endpoint,
        "headers": REQUEST_AUDIT_HEADERS,
        "image_roles": ["IMAGE 1 original", "IMAGE 2 clean"],
        "payload": _redact_data_urls(payload),
    }


def run_experiment(
    original_image: Path,
    clean_image: Path,
    output_dir: Path,
    *,
    client: DualImageChatCompletionsVLMClient,
    model: str,
    runs: int = 1,
) -> dict[str, Any]:
    """Prepare aligned Original/Clean images, run the census, and persist evidence."""

    validate_runs(runs)
    output_dir.mkdir(parents=True, exist_ok=True)
    original_size = read_image_size(original_image)
    clean_size = read_image_size(clean_image)
    if original_size != clean_size:
        raise ValueError(
            "original_and_clean_size_mismatch: "
            f"original={original_size[0]}x{original_size[1]} "
            f"clean={clean_size[0]}x{clean_size[1]}; this experiment requires "
            "the Original and Clean inputs to be strictly spatially aligned"
        )

    original_output = output_dir / "original.png"
    clean_output = output_dir / "source.png"
    original_analysis_output = output_dir / "original-analysis-image.png"
    analysis_output = output_dir / "analysis-image.png"
    transform_output = output_dir / "transform.json"
    original_transform_output = output_dir / "original-transform.json"

    _copy_source_image(original_image, original_output)
    _copy_source_image(clean_image, clean_output)
    prepare_analysis_input(
        original_output,
        original_analysis_output,
        original_transform_output,
        max_width=DEFAULT_MAX_WIDTH,
        force_width=True,
    )
    prepare_analysis_input(
        clean_output,
        analysis_output,
        transform_output,
        max_width=DEFAULT_MAX_WIDTH,
        force_width=True,
    )
    original_analysis_size = read_image_size(original_analysis_output)
    source_size = read_image_size(clean_output)
    analysis_size = read_image_size(analysis_output)
    taxonomy = load_frozen_taxonomy()
    response_schema = build_response_schema(taxonomy, analysis_size)
    user_prompt = build_user_prompt(taxonomy, analysis_size)

    summary_results = []
    for run_number in range(1, runs + 1):
        run_dir = output_dir if runs == 1 else output_dir / f"run-{run_number:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            raw_result = client.infer_json(
                original_image_path=original_analysis_output,
                clean_image_path=analysis_output,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=response_schema,
            )
        except VLMError:
            provider_response = _raw_provider_response(client)
            if provider_response is not None:
                write_json(run_dir / "raw-response.json", provider_response)
            request_audit = _request_audit(client)
            if request_audit is not None:
                write_json(run_dir / "request-audit.json", request_audit)
            raise
        provider_response = _raw_provider_response(client)
        write_json(
            run_dir / "raw-response.json",
            raw_result if provider_response is None else provider_response,
        )
        request_audit = _request_audit(client)
        if request_audit is not None:
            write_json(run_dir / "request-audit.json", request_audit)
        direct_assets = build_direct_assets(
            raw_result,
            source_size,
            analysis_size,
            taxonomy=taxonomy,
        )
        write_json(run_dir / "direct-assets.json", direct_assets)
        render_overlay(
            analysis_output,
            direct_assets,
            run_dir / "overlay-analysis.png",
            bbox_field="bbox_analysis",
        )
        render_overlay(
            original_analysis_output,
            direct_assets,
            run_dir / "overlay-original.png",
            bbox_field="bbox_analysis",
        )
        render_overlay(
            clean_output,
            direct_assets,
            run_dir / "overlay-source.png",
            bbox_field="bbox_source",
        )
        asset_count = len(direct_assets["assets"])
        write_json(
            run_dir / "run-metadata.json",
            {
                "schema_version": SCHEMA_VERSION,
                "input_mode": INPUT_MODE,
                "run": run_number,
                "model": model,
                "original_image": str(original_image),
                "clean_image": str(clean_image),
                "original_image_size": {
                    "width": original_size[0],
                    "height": original_size[1],
                },
                "clean_image_size": {
                    "width": clean_size[0],
                    "height": clean_size[1],
                },
                "original_analysis_image": "original-analysis-image.png",
                "original_analysis_size": {
                    "width": original_analysis_size[0],
                    "height": original_analysis_size[1],
                },
                "source_image": "source.png",
                "source_size": direct_assets["source_image_size"],
                "analysis_image": "analysis-image.png",
                "analysis_size": direct_assets["analysis_image_size"],
                "asset_count": asset_count,
                "timestamp": utc_timestamp(),
                "raw_response_representation": (
                    "decoded provider response envelope when exposed by the VLM client"
                ),
            },
        )
        summary_results.append({"run": run_number, "asset_count": asset_count})

    summary = {
        "schema_version": SCHEMA_VERSION,
        "input_mode": INPUT_MODE,
        "model": model,
        "original_image": str(original_image),
        "clean_image": str(clean_image),
        "original_image_size": {
            "width": original_size[0],
            "height": original_size[1],
        },
        "clean_image_size": {
            "width": clean_size[0],
            "height": clean_size[1],
        },
        "runs": runs,
        "results": summary_results,
        "timestamp": utc_timestamp(),
    }
    write_json(output_dir / "summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a Stage2-A1 dual-image direct-asset census: IMAGE 1 Original UI "
            "as semantic reference, IMAGE 2 Clean UI as the authoritative bbox image."
        )
    )
    parser.add_argument("--original-image", required=True, type=Path)
    parser.add_argument("--clean-image", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override STAGE2A_VLM_MODEL for this experiment.",
    )
    parser.add_argument("--runs", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_runs(args.runs)
        config = replace(
            VLMClientConfig.from_env(model_override=args.model),
            api_mode="chat_completions",
            thinking_policy="omit",
        )
        client = DualImageChatCompletionsVLMClient(
            config,
            max_tokens=DIRECT_ASSET_DISCOVERY_MAX_TOKENS,
        )
        summary = run_experiment(
            args.original_image,
            args.clean_image,
            args.output_dir,
            client=client,
            model=config.model,
            runs=args.runs,
        )
    except (OSError, UnicodeError, ValueError, VLMError) as exc:
        print(f"Dual-image direct asset discovery probe failed: {exc}", file=sys.stderr)
        return 1
    counts = ", ".join(
        f"run-{item['run']:03d}={item['asset_count']}"
        for item in summary["results"]
    )
    print(f"Completed {summary['runs']} dual-image direct asset census run(s): {counts}")
    print(f"Wrote experiment outputs to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
