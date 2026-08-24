#!/usr/bin/env python3
"""Build deterministic Stage2 v0.1 asset-analysis.json from model candidates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from validate_asset_analysis import (
    candidate_sort_key,
    load_json,
    read_image_size,
    validate_analysis,
    validate_candidates,
)


SCHEMA_VERSION = "0.1"
TAXONOMY_VERSION = "game-ui-asset-taxonomy-v0.1"


def map_bbox_to_source(
    bbox: dict[str, int],
    analysis_size: tuple[int, int],
    source_size: tuple[int, int],
) -> dict[str, int]:
    """Map all four bbox edges and clamp the result to source image bounds."""

    analysis_width, analysis_height = analysis_size
    source_width, source_height = source_size
    scale_x = source_width / analysis_width
    scale_y = source_height / analysis_height

    source_x1 = min(source_width, max(0, round(bbox["x"] * scale_x)))
    source_y1 = min(source_height, max(0, round(bbox["y"] * scale_y)))
    source_x2 = min(
        source_width,
        max(0, round((bbox["x"] + bbox["width"]) * scale_x)),
    )
    source_y2 = min(
        source_height,
        max(0, round((bbox["y"] + bbox["height"]) * scale_y)),
    )

    if source_x2 <= source_x1:
        source_x1 = min(source_x1, source_width - 1)
        source_x2 = source_x1 + 1
    if source_y2 <= source_y1:
        source_y1 = min(source_y1, source_height - 1)
        source_y2 = source_y1 + 1

    return {
        "x": source_x1,
        "y": source_y1,
        "width": source_x2 - source_x1,
        "height": source_y2 - source_y1,
    }


def build_analysis(
    source_image: Path,
    candidates: Any,
    analysis_image: Path | None = None,
) -> dict[str, Any]:
    source_size = read_image_size(source_image)
    analysis_size = (
        read_image_size(analysis_image) if analysis_image is not None else source_size
    )
    candidate_errors = validate_candidates(
        candidates,
        analysis_size,
        bounds_name="analysis image" if analysis_image is not None else "source",
    )
    if candidate_errors:
        raise ValueError("invalid asset candidates:\n- " + "\n- ".join(candidate_errors))

    mapped_candidates = [
        {
            **candidate,
            "bbox": map_bbox_to_source(candidate["bbox"], analysis_size, source_size),
        }
        for candidate in candidates
    ]
    ordered = sorted(mapped_candidates, key=candidate_sort_key)
    counters: Counter[str] = Counter()
    assets: list[dict[str, Any]] = []
    for candidate in ordered:
        semantic_type = candidate["semantic_type"]
        counters[semantic_type] += 1
        asset = {
            "id": f"{semantic_type}_{counters[semantic_type]:03d}",
            **candidate,
        }
        assets.append(asset)

    analysis = {
        "schema_version": SCHEMA_VERSION,
        "source_image": source_image.name,
        "source_size": {"width": source_size[0], "height": source_size[1]},
        "taxonomy_version": TAXONOMY_VERSION,
        "assets": assets,
    }
    analysis_errors = validate_analysis(analysis, source_image)
    if analysis_errors:
        raise ValueError("built asset analysis is invalid:\n- " + "\n- ".join(analysis_errors))
    return analysis


def build_file(
    source_image: Path,
    model_output: Path,
    output: Path,
    analysis_image: Path | None = None,
) -> dict[str, Any]:
    try:
        candidates = load_json(model_output)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid JSON in {model_output}: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unable to read model output {model_output}: {exc}") from exc

    analysis = build_analysis(source_image, candidates, analysis_image)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build deterministic asset-analysis.json from visual-model candidates."
    )
    parser.add_argument("--source-image", required=True, type=Path)
    parser.add_argument(
        "--analysis-image",
        type=Path,
        help="image analyzed by the model; defaults to --source-image for compatibility",
    )
    parser.add_argument("--model-output", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        analysis = build_file(
            args.source_image,
            args.model_output,
            args.output,
            args.analysis_image,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(analysis['assets'])} asset(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
