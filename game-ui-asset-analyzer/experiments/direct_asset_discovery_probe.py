#!/usr/bin/env python3
"""Run a direct, non-recursive terminal-asset census on one clean UI image."""

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
    VLMClient,
    VLMClientConfig,
    VLMError,
    create_configured_vlm_client,
)


SCHEMA_VERSION = "0.1"
DIRECT_ASSET_DISCOVERY_MAX_TOKENS = 12000
TAXONOMY_REFERENCE_PATH = ROOT / "references" / "asset-taxonomy.md"
SYSTEM_PROMPT = """You are performing a direct visual asset census of a game UI.
Analyze only the attached Analysis Image and return exactly one JSON object.
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
    return f"""Perform a direct visual asset census of this clean game UI.

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


def run_experiment(
    image: Path,
    output_dir: Path,
    *,
    client: VLMClient,
    model: str,
    runs: int = 1,
) -> dict[str, Any]:
    """Prepare one Analysis Image, make independent census calls, and persist evidence."""

    validate_runs(runs)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_output = output_dir / "source.png"
    analysis_output = output_dir / "analysis-image.png"
    transform_output = output_dir / "transform.json"

    _copy_source_image(image, source_output)
    prepare_analysis_input(
        source_output,
        analysis_output,
        transform_output,
        max_width=DEFAULT_MAX_WIDTH,
        force_width=True,
    )
    source_size = read_image_size(source_output)
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
                image_path=analysis_output,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=response_schema,
            )
        except VLMError:
            provider_response = _raw_provider_response(client)
            if provider_response is not None:
                write_json(run_dir / "raw-response.json", provider_response)
            raise
        provider_response = _raw_provider_response(client)
        write_json(
            run_dir / "raw-response.json",
            raw_result if provider_response is None else provider_response,
        )
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
            source_output,
            direct_assets,
            run_dir / "overlay-source.png",
            bbox_field="bbox_source",
        )
        asset_count = len(direct_assets["assets"])
        write_json(
            run_dir / "run-metadata.json",
            {
                "schema_version": SCHEMA_VERSION,
                "run": run_number,
                "model": model,
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
        "model": model,
        "runs": runs,
        "results": summary_results,
        "timestamp": utc_timestamp(),
    }
    write_json(output_dir / "summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a direct, non-recursive Stage2-A terminal-asset census on a clean UI."
        )
    )
    parser.add_argument("--image", required=True, type=Path)
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
        client = create_configured_vlm_client(
            config,
            max_tokens=DIRECT_ASSET_DISCOVERY_MAX_TOKENS,
        )
        summary = run_experiment(
            args.image,
            args.output_dir,
            client=client,
            model=config.model,
            runs=args.runs,
        )
    except (OSError, UnicodeError, ValueError, VLMError) as exc:
        print(f"Direct asset discovery probe failed: {exc}", file=sys.stderr)
        return 1
    counts = ", ".join(
        f"run-{item['run']:03d}={item['asset_count']}"
        for item in summary["results"]
    )
    print(f"Completed {summary['runs']} direct asset census run(s): {counts}")
    print(f"Wrote experiment outputs to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
