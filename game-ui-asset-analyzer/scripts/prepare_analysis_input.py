#!/usr/bin/env python3
"""Prepare a deterministic PNG for visual bbox analysis and write size metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


DEFAULT_MAX_WIDTH = 1024
PNG_COMPRESS_LEVEL = 9


def calculate_analysis_size(
    source_size: tuple[int, int],
    max_width: int = DEFAULT_MAX_WIDTH,
) -> tuple[int, int]:
    """Return a proportional size capped by max_width without upscaling."""

    source_width, source_height = source_size
    if source_width <= 0 or source_height <= 0:
        raise ValueError(f"source image has invalid dimensions: {source_width}x{source_height}")
    if max_width <= 0:
        raise ValueError("max width must be greater than zero")
    if source_width <= max_width:
        return source_width, source_height
    analysis_height = max(1, round(source_height * max_width / source_width))
    return max_width, analysis_height


def build_metadata(
    source_image: Path,
    source_size: tuple[int, int],
    analysis_image: Path,
    analysis_size: tuple[int, int],
) -> dict[str, Any]:
    source_width, source_height = source_size
    analysis_width, analysis_height = analysis_size
    return {
        "source_image": source_image.name,
        "source_size": {"width": source_width, "height": source_height},
        "analysis_image": analysis_image.name,
        "analysis_size": {"width": analysis_width, "height": analysis_height},
        "scale_to_source": {
            "x": source_width / analysis_width,
            "y": source_height / analysis_height,
        },
    }


def _png_compatible(image: Image.Image) -> Image.Image:
    if image.mode in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
        return image
    return image.convert("RGB")


def _resize_ready(image: Image.Image) -> Image.Image:
    """Return a mode for which Pillow applies high-quality LANCZOS resampling."""

    if image.mode == "P":
        return image.convert("RGBA" if "transparency" in image.info else "RGB")
    if image.mode == "1":
        return image.convert("L")
    return _png_compatible(image)


def prepare_analysis_input(
    source_image: Path,
    output_image: Path,
    metadata_output: Path,
    max_width: int = DEFAULT_MAX_WIDTH,
) -> dict[str, Any]:
    """Read the real source image, write the capped PNG, and return metadata."""

    if not source_image.is_file():
        raise ValueError(f"source image does not exist or is not a file: {source_image}")
    try:
        with Image.open(source_image) as opened:
            opened.load()
            source_size = opened.size
            analysis_size = calculate_analysis_size(source_size, max_width)
            if analysis_size != source_size:
                prepared = _resize_ready(opened).resize(
                    analysis_size,
                    Image.Resampling.LANCZOS,
                )
            else:
                prepared = _png_compatible(opened).copy()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read source image {source_image}: {exc}") from exc

    output_image.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        prepared.save(
            output_image,
            format="PNG",
            compress_level=PNG_COMPRESS_LEVEL,
        )
    except OSError as exc:
        raise ValueError(f"unable to write analysis image {output_image}: {exc}") from exc

    metadata = build_metadata(source_image, source_size, output_image, analysis_size)
    try:
        metadata_output.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unable to write metadata {metadata_output}: {exc}") from exc
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare a deterministic, width-capped PNG for visual bbox analysis."
    )
    parser.add_argument("--source-image", required=True, type=Path)
    parser.add_argument("--output-image", required=True, type=Path)
    parser.add_argument("--metadata-output", required=True, type=Path)
    parser.add_argument("--max-width", type=int, default=DEFAULT_MAX_WIDTH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metadata = prepare_analysis_input(
            args.source_image,
            args.output_image,
            args.metadata_output,
            args.max_width,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Preparation failed: {exc}", file=sys.stderr)
        return 1
    source = metadata["source_size"]
    analysis = metadata["analysis_size"]
    print(
        f"Prepared {args.output_image}: "
        f"{source['width']}x{source['height']} -> "
        f"{analysis['width']}x{analysis['height']}"
    )
    print(f"Wrote metadata to {args.metadata_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
