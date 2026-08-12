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


def build_analysis(source_image: Path, candidates: Any) -> dict[str, Any]:
    width, height = read_image_size(source_image)
    candidate_errors = validate_candidates(candidates, (width, height))
    if candidate_errors:
        raise ValueError("invalid asset candidates:\n- " + "\n- ".join(candidate_errors))

    ordered = sorted(candidates, key=candidate_sort_key)
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
        "source_size": {"width": width, "height": height},
        "taxonomy_version": TAXONOMY_VERSION,
        "assets": assets,
    }
    analysis_errors = validate_analysis(analysis, source_image)
    if analysis_errors:
        raise ValueError("built asset analysis is invalid:\n- " + "\n- ".join(analysis_errors))
    return analysis


def build_file(source_image: Path, model_output: Path, output: Path) -> dict[str, Any]:
    try:
        candidates = load_json(model_output)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid JSON in {model_output}: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"unable to read model output {model_output}: {exc}") from exc

    analysis = build_analysis(source_image, candidates)
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
    parser.add_argument("--model-output", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        analysis = build_file(args.source_image, args.model_output, args.output)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(analysis['assets'])} asset(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
