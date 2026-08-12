#!/usr/bin/env python3
"""Validate Stage2 v0.1 candidate or final asset-analysis JSON documents."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_SCHEMA_PATH = ROOT / "schemas" / "asset-candidates.schema.json"
ANALYSIS_SCHEMA_PATH = ROOT / "schemas" / "asset-analysis.schema.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema(path: Path) -> dict[str, Any]:
    schema = load_json(path)
    if not isinstance(schema, dict):
        raise ValueError("schema root must be an object")
    Draft202012Validator.check_schema(schema)
    return schema


def json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_schema(data: Any, schema_path: Path) -> list[str]:
    try:
        schema = load_schema(schema_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"$: unable to load schema {schema_path}: {exc}"]

    validator = Draft202012Validator(schema)
    return [
        f"{json_path(error.absolute_path)}: {error.message}"
        for error in sorted(
            validator.iter_errors(data),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                item.message,
            ),
        )
    ]


def read_image_size(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise ValueError(f"source image does not exist or is not a file: {path}")
    try:
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read source image {path}: {exc}") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"source image has invalid dimensions: {width}x{height}")
    return width, height


def _validate_bbox_bounds(
    items: Any,
    image_size: tuple[int, int],
    root_path: str,
    bounds_name: str = "source",
) -> list[str]:
    if not isinstance(items, list):
        return []
    image_width, image_height = image_size
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("bbox"), dict):
            continue
        bbox = item["bbox"]
        values = [bbox.get(name) for name in ("x", "y", "width", "height")]
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            continue
        x, y, width, height = values
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            continue
        if x + width > image_width:
            errors.append(
                f"{root_path}[{index}].bbox: right edge {x + width} exceeds "
                f"{bounds_name} width {image_width}"
            )
        if y + height > image_height:
            errors.append(
                f"{root_path}[{index}].bbox: bottom edge {y + height} exceeds "
                f"{bounds_name} height {image_height}"
            )
    return errors


def validate_candidates(
    data: Any,
    image_size: tuple[int, int] | None = None,
    bounds_name: str = "source",
) -> list[str]:
    errors = validate_schema(data, CANDIDATE_SCHEMA_PATH)
    if image_size is not None:
        errors.extend(_validate_bbox_bounds(data, image_size, "$", bounds_name))
    return errors


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    bbox = candidate["bbox"]
    return bbox["y"], bbox["x"], bbox["width"], bbox["height"]


def _validate_order_and_ids(assets: Any) -> list[str]:
    if not isinstance(assets, list) or not all(isinstance(asset, dict) for asset in assets):
        return []
    if not all(
        isinstance(asset.get("bbox"), dict)
        and all(
            isinstance(asset["bbox"].get(field), int)
            and not isinstance(asset["bbox"].get(field), bool)
            for field in ("x", "y", "width", "height")
        )
        for asset in assets
    ):
        return []

    errors: list[str] = []
    sorted_assets = sorted(assets, key=candidate_sort_key)
    if assets != sorted_assets:
        errors.append("$.assets: assets are not sorted by y, x, width, height")

    counters: Counter[str] = Counter()
    seen_ids: set[str] = set()
    for index, asset in enumerate(assets):
        semantic_type = asset.get("semantic_type")
        if not isinstance(semantic_type, str):
            continue
        counters[semantic_type] += 1
        expected = f"{semantic_type}_{counters[semantic_type]:03d}"
        if asset.get("id") != expected:
            errors.append(f"$.assets[{index}].id: expected deterministic id {expected!r}")
        asset_id = asset.get("id")
        if isinstance(asset_id, str):
            if asset_id in seen_ids:
                errors.append(f"$.assets[{index}].id: duplicate asset id {asset_id!r}")
            seen_ids.add(asset_id)
    return errors


def validate_analysis(
    data: Any,
    source_image: Path | None = None,
) -> list[str]:
    errors = validate_schema(data, ANALYSIS_SCHEMA_PATH)
    if not isinstance(data, dict):
        return errors

    source_size = data.get("source_size")
    if isinstance(source_size, dict):
        width = source_size.get("width")
        height = source_size.get("height")
        if (
            isinstance(width, int)
            and not isinstance(width, bool)
            and isinstance(height, int)
            and not isinstance(height, bool)
            and width > 0
            and height > 0
        ):
            errors.extend(_validate_bbox_bounds(data.get("assets"), (width, height), "$.assets"))

    errors.extend(_validate_order_and_ids(data.get("assets")))

    if source_image is not None:
        try:
            actual_width, actual_height = read_image_size(source_image)
        except ValueError as exc:
            errors.append(f"$: {exc}")
        else:
            if data.get("source_image") != source_image.name:
                errors.append(
                    f"$.source_image: expected {source_image.name!r} from the source image path"
                )
            if source_size != {"width": actual_width, "height": actual_height}:
                errors.append(
                    "$.source_size: does not match actual source image dimensions "
                    f"{actual_width}x{actual_height}"
                )
    return errors


def validate_file(input_path: Path, source_image: Path | None = None) -> list[str]:
    try:
        data = load_json(input_path)
    except json.JSONDecodeError as exc:
        return [
            f"$: invalid JSON in {input_path}: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ]
    except (OSError, UnicodeError) as exc:
        return [f"$: unable to read {input_path}: {exc}"]
    return validate_analysis(data, source_image)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Stage2 v0.1 asset-analysis.json.")
    parser.add_argument("input", type=Path, help="asset-analysis.json to validate")
    parser.add_argument(
        "--source-image",
        type=Path,
        help="optional original image; verifies existence, file name, and actual dimensions",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_file(args.input, args.source_image)
    if errors:
        print(f"Validation failed for {args.input}:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Valid asset analysis: {args.input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
