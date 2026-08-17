#!/usr/bin/env python3
"""Render deterministic expand_instances boxes for human review."""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

import render_structural_overlay as overlay_layout
import validate_expand_instances


WRAP_WIDTH = 32


def _label_lines(
    instance_id: str,
    instance_type: str,
    partial: bool,
    font: ImageFont.ImageFont,
) -> list[str]:
    suffix = " [partial]" if partial else ""
    label = overlay_layout._safe_label(
        f"{instance_id} {instance_type}{suffix}",
        font,
    )
    return textwrap.wrap(
        label,
        width=WRAP_WIDTH,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [label]


def render_overlay(
    analysis_image: Path,
    instances_document: Path,
    output_image: Path,
) -> None:
    """Render validated Analysis Image bboxes without changing the JSON document."""

    errors = validate_expand_instances.validate_file(
        instances_document,
        analysis_image,
    )
    if errors:
        raise ValueError("invalid expanded instances: " + "; ".join(errors))
    try:
        data = json.loads(instances_document.read_text(encoding="utf-8"))
        with Image.open(analysis_image) as opened:
            opened.load()
            canvas = opened.convert("RGB")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {instances_document}: {exc}") from exc
    except (OSError, UnicodeError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read overlay input: {exc}") from exc

    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    occupied: list[tuple[int, int, int, int]] = []

    for index, instance in enumerate(data["instances"]):
        color = overlay_layout.COLORS[index % len(overlay_layout.COLORS)]
        bbox = instance["bbox"]
        left = bbox["x"]
        top = bbox["y"]
        right = left + bbox["width"] - 1
        bottom = top + bbox["height"] - 1
        draw.rectangle(
            (left, top, right, bottom),
            outline=color,
            width=overlay_layout.BOX_WIDTH,
        )

        lines = _label_lines(
            instance["id"],
            data["instance_type"],
            instance["partial_instance"],
            font,
        )
        text_width, text_height, line_height = overlay_layout._text_size(
            draw,
            lines,
            font,
        )
        block_width = text_width + overlay_layout.LABEL_PADDING * 2
        block_height = text_height + overlay_layout.LABEL_PADDING * 2
        label_rect = overlay_layout._place_label(
            bbox,
            (block_width, block_height),
            canvas.size,
            occupied,
        )
        occupied.append(label_rect)
        draw.rectangle(label_rect, fill=color)
        text_x = label_rect[0] + overlay_layout.LABEL_PADDING
        text_y = label_rect[1] + overlay_layout.LABEL_PADDING
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
        description="Render a deterministic expand_instances v0.1 review overlay."
    )
    parser.add_argument("--analysis-image", required=True, type=Path)
    parser.add_argument("--instances", required=True, type=Path)
    parser.add_argument("--output-image", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        render_overlay(args.analysis_image, args.instances, args.output_image)
    except ValueError as exc:
        print(f"Overlay rendering failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote instances overlay to {args.output_image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
