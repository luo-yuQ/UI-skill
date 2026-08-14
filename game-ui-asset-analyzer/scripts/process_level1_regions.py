#!/usr/bin/env python3
"""Create deterministic Level-1 ROI crops, transforms, and a QA overlay."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from validate_level1_regions import load_json, read_image_size, validate_processed, validate_raw


DEFAULT_PADDING_RATIO = 0.06
DEFAULT_MIN_OUTPUT_SHORT_SIDE = 768
DEFAULT_MAX_UPSCALE = 2.0
OVERLAY_COLORS = (
    "#ff4d4f",
    "#40a9ff",
    "#73d13d",
    "#ffc53d",
    "#9254de",
    "#36cfc9",
)


def clamp_bbox(bbox: dict[str, int], source_size: tuple[int, int]) -> dict[str, int]:
    """Intersect a bbox with source bounds and reject an empty intersection."""

    source_width, source_height = source_size
    x1 = max(0, min(source_width, bbox["x"]))
    y1 = max(0, min(source_height, bbox["y"]))
    x2 = max(0, min(source_width, bbox["x"] + bbox["width"]))
    y2 = max(0, min(source_height, bbox["y"] + bbox["height"]))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"bbox has no area inside the source image: {bbox}")
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def build_analysis_bbox(
    bbox: dict[str, int], source_size: tuple[int, int], padding_ratio: float
) -> tuple[dict[str, int], dict[str, Any]]:
    """Pad a source bbox by its own axes, clamp it, and report actual padding."""

    pad_x = round(bbox["width"] * padding_ratio)
    pad_y = round(bbox["height"] * padding_ratio)
    expanded = {
        "x": bbox["x"] - pad_x,
        "y": bbox["y"] - pad_y,
        "width": bbox["width"] + 2 * pad_x,
        "height": bbox["height"] + 2 * pad_y,
    }
    analysis_bbox = clamp_bbox(expanded, source_size)
    actual = {
        "left": bbox["x"] - analysis_bbox["x"],
        "top": bbox["y"] - analysis_bbox["y"],
        "right": analysis_bbox["x"]
        + analysis_bbox["width"]
        - bbox["x"]
        - bbox["width"],
        "bottom": analysis_bbox["y"]
        + analysis_bbox["height"]
        - bbox["y"]
        - bbox["height"],
    }
    return analysis_bbox, {
        "ratio": padding_ratio,
        "requested_pixels": {"x": pad_x, "y": pad_y},
        "actual_pixels": actual,
    }


def choose_output_size(
    crop_size: tuple[int, int], min_output_short_side: int, max_upscale: float
) -> tuple[int, int]:
    """Upscale only when the short side is below the configured target."""

    width, height = crop_size
    short_side = min(width, height)
    if min_output_short_side <= 0 or short_side >= min_output_short_side:
        return crop_size
    target_scale = min(max_upscale, min_output_short_side / short_side)
    if target_scale <= 1:
        return crop_size
    return max(1, round(width * target_scale)), max(1, round(height * target_scale))


def output_bbox_to_source(
    bbox: dict[str, float], transform: dict[str, float]
) -> dict[str, float]:
    """Map an output-crop local bbox back to original source-image coordinates."""

    return {
        "x": transform["source_x"] + bbox["x"] / transform["scale_x"],
        "y": transform["source_y"] + bbox["y"] / transform["scale_y"],
        "width": bbox["width"] / transform["scale_x"],
        "height": bbox["height"] / transform["scale_y"],
    }


def source_bbox_to_output(
    bbox: dict[str, float], transform: dict[str, float]
) -> dict[str, float]:
    """Map a source-image bbox into output-crop local coordinates."""

    return {
        "x": (bbox["x"] - transform["source_x"]) * transform["scale_x"],
        "y": (bbox["y"] - transform["source_y"]) * transform["scale_y"],
        "width": bbox["width"] * transform["scale_x"],
        "height": bbox["height"] * transform["scale_y"],
    }


def _draw_dashed_rectangle(
    draw: ImageDraw.ImageDraw,
    bbox: dict[str, int],
    color: str,
    width: int = 1,
    dash: int = 7,
) -> None:
    x1, y1 = bbox["x"], bbox["y"]
    x2, y2 = x1 + bbox["width"] - 1, y1 + bbox["height"] - 1
    for x in range(x1, x2 + 1, dash * 2):
        draw.line((x, y1, min(x + dash, x2), y1), fill=color, width=width)
        draw.line((x, y2, min(x + dash, x2), y2), fill=color, width=width)
    for y in range(y1, y2 + 1, dash * 2):
        draw.line((x1, y, x1, min(y + dash, y2)), fill=color, width=width)
        draw.line((x2, y, x2, min(y + dash, y2)), fill=color, width=width)


def _overlay_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        return ImageFont.load_default()


def create_overlay(
    source: Image.Image, regions: list[dict[str, Any]], output_path: Path
) -> None:
    overlay = source.convert("RGB")
    draw = ImageDraw.Draw(overlay)
    font = _overlay_font()
    for index, region in enumerate(regions):
        color = OVERLAY_COLORS[index % len(OVERLAY_COLORS)]
        bbox = region["bbox"]
        analysis_bbox = region["analysis_bbox"]
        _draw_dashed_rectangle(draw, analysis_bbox, color)
        x1, y1 = bbox["x"], bbox["y"]
        x2, y2 = x1 + bbox["width"] - 1, y1 + bbox["height"] - 1
        draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
        label = f"{region['id']} / {region['label']}"
        try:
            text_box = draw.textbbox((0, 0), label, font=font)
            text_width = text_box[2] - text_box[0]
            text_height = text_box[3] - text_box[1]
            text_y = max(0, y1 - text_height - 6)
            text_x = min(max(0, x1), max(0, overlay.width - text_width - 6))
            draw.rectangle(
                (text_x, text_y, text_x + text_width + 6, text_y + text_height + 6),
                fill=color,
            )
            draw.text((text_x + 3, text_y + 3), label, fill="black", font=font)
        except UnicodeError:
            safe_label = label.encode("ascii", "replace").decode("ascii")
            draw.text((max(0, x1), max(0, y1)), safe_label, fill=color, font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(output_path, format="PNG")


def _relative_output_path(path: Path, document_path: Path) -> str:
    relative = os.path.relpath(path.resolve(), document_path.parent.resolve())
    return Path(relative).as_posix()


def process_regions(
    raw_data: Any,
    source_image: Path,
    output_json: Path,
    crops_dir: Path,
    overlay_output: Path,
    *,
    padding_ratio: float = DEFAULT_PADDING_RATIO,
    min_output_short_side: int = DEFAULT_MIN_OUTPUT_SHORT_SIDE,
    max_upscale: float = DEFAULT_MAX_UPSCALE,
) -> dict[str, Any]:
    if not math.isfinite(padding_ratio) or padding_ratio < 0:
        raise ValueError("padding_ratio must be a finite number >= 0")
    if type(min_output_short_side) is not int or min_output_short_side < 0:
        raise ValueError("min_output_short_side must be an integer >= 0")
    if not math.isfinite(max_upscale) or max_upscale < 1:
        raise ValueError("max_upscale must be a finite number >= 1")

    source_size = read_image_size(source_image)
    raw_errors = validate_raw(raw_data, source_image)
    if raw_errors:
        raise ValueError("invalid raw Level-1 regions:\n- " + "\n- ".join(raw_errors))

    with Image.open(source_image) as opened:
        source = opened.copy()

    crops_dir.mkdir(parents=True, exist_ok=True)
    processed_regions: list[dict[str, Any]] = []
    for raw_region in raw_data["regions"]:
        # Raw input is never mutated; this defensive clamp is a no-op for valid input.
        bbox = clamp_bbox(raw_region["bbox"], source_size)
        analysis_bbox, padding = build_analysis_bbox(bbox, source_size, padding_ratio)
        crop = source.crop(
            (
                analysis_bbox["x"],
                analysis_bbox["y"],
                analysis_bbox["x"] + analysis_bbox["width"],
                analysis_bbox["y"] + analysis_bbox["height"],
            )
        )
        output_size = choose_output_size(
            crop.size, min_output_short_side, max_upscale
        )
        if output_size != crop.size:
            crop = crop.resize(output_size, Image.Resampling.LANCZOS)

        crop_path = crops_dir / f"{raw_region['id']}.png"
        crop.save(crop_path, format="PNG")
        scale_x = output_size[0] / analysis_bbox["width"]
        scale_y = output_size[1] / analysis_bbox["height"]
        transform = {
            "source_x": analysis_bbox["x"],
            "source_y": analysis_bbox["y"],
            "source_width": analysis_bbox["width"],
            "source_height": analysis_bbox["height"],
            "output_width": output_size[0],
            "output_height": output_size[1],
            "scale_x": scale_x,
            "scale_y": scale_y,
        }
        processed_region = {
            **copy.deepcopy(raw_region),
            "bbox": bbox,
            "analysis_bbox": analysis_bbox,
            "padding": padding,
            "output_crop": _relative_output_path(crop_path, output_json),
            "upscale": {
                "applied": output_size != (analysis_bbox["width"], analysis_bbox["height"]),
                "scale_x": scale_x,
                "scale_y": scale_y,
            },
            "transform": transform,
        }
        processed_regions.append(processed_region)

    result = {
        "schema_version": raw_data["schema_version"],
        "source_image": raw_data["source_image"],
        "source_size": copy.deepcopy(raw_data["source_size"]),
        "background_root": copy.deepcopy(raw_data["background_root"]),
        "processing": {
            "padding_ratio": padding_ratio,
            "min_output_short_side": min_output_short_side,
            "max_upscale": max_upscale,
        },
        "regions": processed_regions,
    }
    processed_errors = validate_processed(result, output_json, source_image)
    if processed_errors:
        raise ValueError(
            "processed Level-1 regions are invalid:\n- " + "\n- ".join(processed_errors)
        )

    create_overlay(source, processed_regions, overlay_output)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def process_file(
    raw_json: Path,
    source_image: Path,
    output_json: Path,
    crops_dir: Path,
    overlay_output: Path,
    **options: Any,
) -> dict[str, Any]:
    if raw_json.resolve() == output_json.resolve():
        raise ValueError("output_json must not overwrite the raw VLM JSON")
    try:
        raw_data = load_json(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid JSON in {raw_json}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unable to read raw Level-1 JSON {raw_json}: {exc}") from exc
    return process_regions(
        raw_data,
        source_image,
        output_json,
        crops_dir,
        overlay_output,
        **options,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process raw VLM Level-1 regions into deterministic ROI artifacts."
    )
    parser.add_argument("--raw-json", required=True, type=Path)
    parser.add_argument("--source-image", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--crops-dir", required=True, type=Path)
    parser.add_argument("--overlay-output", required=True, type=Path)
    parser.add_argument("--padding-ratio", type=float, default=DEFAULT_PADDING_RATIO)
    parser.add_argument(
        "--min-output-short-side", type=int, default=DEFAULT_MIN_OUTPUT_SHORT_SIDE
    )
    parser.add_argument("--max-upscale", type=float, default=DEFAULT_MAX_UPSCALE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = process_file(
            args.raw_json,
            args.source_image,
            args.output_json,
            args.crops_dir,
            args.overlay_output,
            padding_ratio=args.padding_ratio,
            min_output_short_side=args.min_output_short_side,
            max_upscale=args.max_upscale,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Level-1 processing failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Wrote {len(result['regions'])} region crop(s), metadata {args.output_json}, "
        f"and overlay {args.overlay_output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
