#!/usr/bin/env python3
"""Render deterministic structural_split boxes for human review."""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

import validate_structural_split


COLORS = (
    "#FF3B30",
    "#34C759",
    "#007AFF",
    "#FF9500",
    "#AF52DE",
    "#00C7BE",
    "#FF2D55",
    "#5856D6",
)
BOX_WIDTH = 3
LABEL_PADDING = 3
MAX_LABEL_CHARACTERS = 64
WRAP_WIDTH = 32


def _safe_label(value: str, font: ImageFont.ImageFont) -> str:
    value = value.strip()
    if len(value) > MAX_LABEL_CHARACTERS:
        value = value[: MAX_LABEL_CHARACTERS - 3].rstrip() + "..."
    try:
        font.getbbox(value)
        return value
    except (AttributeError, UnicodeEncodeError):
        escaped = value.encode("ascii", "backslashreplace").decode("ascii")
        if len(escaped) > MAX_LABEL_CHARACTERS:
            escaped = escaped[: MAX_LABEL_CHARACTERS - 3].rstrip() + "..."
        return escaped


def _label_lines(child: dict[str, Any], font: ImageFont.ImageFont) -> list[str]:
    label = _safe_label(f"{child['id']} {child['label']}", font)
    return textwrap.wrap(
        label,
        width=WRAP_WIDTH,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [label]


def _text_size(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
) -> tuple[int, int, int]:
    sample = draw.textbbox((0, 0), "Ag", font=font)
    line_height = max(1, sample[3] - sample[1])
    widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
    width = max(widths, default=1)
    height = line_height * len(lines) + max(0, len(lines) - 1) * 2
    return width, height, line_height


def _clamp_label_rect(
    left: int,
    top: int,
    width: int,
    height: int,
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    image_width, image_height = image_size
    right = min(image_width, max(width, left + width))
    bottom = min(image_height, max(height, top + height))
    left = max(0, right - width)
    top = max(0, bottom - height)
    return left, top, right, bottom


def _intersection_area(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> int:
    width = max(0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def _place_label(
    bbox: dict[str, int],
    block_size: tuple[int, int],
    image_size: tuple[int, int],
    occupied: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    x, y = bbox["x"], bbox["y"]
    box_right = x + bbox["width"]
    box_bottom = y + bbox["height"]
    width, height = block_size
    candidates = [
        (x, y - height - 2),
        (x + 2, y + 2),
        (x, box_bottom + 2),
        (box_right - width, y + 2),
    ]
    candidates.extend((x, y + offset) for offset in range(16, 97, 16))
    rects = [
        _clamp_label_rect(left, top, width, height, image_size)
        for left, top in candidates
    ]
    return min(
        rects,
        key=lambda rect: (
            sum(_intersection_area(rect, other) for other in occupied),
            rect[1],
            rect[0],
        ),
    )


def render_overlay(
    analysis_image: Path,
    structural_split: Path,
    output_image: Path,
) -> None:
    """Render validated Analysis Image bboxes without changing the JSON document."""

    errors = validate_structural_split.validate_file(structural_split, analysis_image)
    if errors:
        raise ValueError("invalid structural split: " + "; ".join(errors))
    try:
        data = json.loads(structural_split.read_text(encoding="utf-8"))
        with Image.open(analysis_image) as opened:
            opened.load()
            canvas = opened.convert("RGB")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {structural_split}: {exc}") from exc
    except (OSError, UnicodeError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read overlay input: {exc}") from exc

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    occupied: list[tuple[int, int, int, int]] = []

    for index, child in enumerate(data["children"]):
        color = COLORS[index % len(COLORS)]
        bbox = child["bbox"]
        left = bbox["x"]
        top = bbox["y"]
        right = left + bbox["width"] - 1
        bottom = top + bbox["height"] - 1
        draw.rectangle((left, top, right, bottom), outline=color, width=BOX_WIDTH)

        lines = _label_lines(child, font)
        text_width, text_height, line_height = _text_size(draw, lines, font)
        block_width = text_width + LABEL_PADDING * 2
        block_height = text_height + LABEL_PADDING * 2
        label_rect = _place_label(
            bbox,
            (block_width, block_height),
            canvas.size,
            occupied,
        )
        occupied.append(label_rect)
        draw.rectangle(label_rect, fill=color)
        text_x = label_rect[0] + LABEL_PADDING
        text_y = label_rect[1] + LABEL_PADDING
        for line in lines:
            draw.text((text_x, text_y), line, fill="white", font=font)
            text_y += line_height + 2

    output_image.parent.mkdir(parents=True, exist_ok=True)
    try:
        canvas.save(output_image, format="PNG", compress_level=9)
    except OSError as exc:
        raise ValueError(f"unable to write overlay {output_image}: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a deterministic structural_split v0.1 review overlay."
    )
    parser.add_argument("--analysis-image", required=True, type=Path)
    parser.add_argument("--structural-split", required=True, type=Path)
    parser.add_argument("--output-image", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        render_overlay(
            args.analysis_image,
            args.structural_split,
            args.output_image,
        )
    except ValueError as exc:
        print(f"Overlay rendering failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote structural overlay to {args.output_image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
