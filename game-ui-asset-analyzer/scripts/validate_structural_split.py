#!/usr/bin/env python3
"""Validate a Stage2-A structural_split v0.1 VLM document."""

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
SCHEMA_PATH = ROOT / "schemas" / "structural-split.schema.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema() -> dict[str, Any]:
    schema = load_json(SCHEMA_PATH)
    if not isinstance(schema, dict):
        raise ValueError("schema root must be an object")
    Draft202012Validator.check_schema(schema)
    return schema


def json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def read_image_size(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise ValueError(f"analysis image does not exist or is not a file: {path}")
    try:
        with Image.open(path) as image:
            size = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read analysis image {path}: {exc}") from exc
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError(f"analysis image has invalid dimensions: {size[0]}x{size[1]}")
    return size


def validate_schema(data: Any) -> list[str]:
    try:
        schema = load_schema()
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"$: unable to load schema {SCHEMA_PATH}: {exc}"]

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


def _validate_child_ids(children: Any) -> list[str]:
    if not isinstance(children, list):
        return []
    ids = [
        child.get("id")
        for child in children
        if isinstance(child, dict) and isinstance(child.get("id"), str)
    ]
    return [
        f"$.children: duplicate child id {child_id!r}"
        for child_id, count in Counter(ids).items()
        if count > 1
    ]


def _validate_bbox_bounds(children: Any, image_size: tuple[int, int]) -> list[str]:
    if not isinstance(children, list):
        return []
    image_width, image_height = image_size
    errors: list[str] = []
    for index, child in enumerate(children):
        bbox = child.get("bbox") if isinstance(child, dict) else None
        if not isinstance(bbox, dict):
            continue
        values = [bbox.get(key) for key in ("x", "y", "width", "height")]
        if not all(type(value) is int for value in values):
            continue
        x, y, width, height = values
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            continue
        if x + width > image_width:
            errors.append(
                f"$.children[{index}].bbox: right edge {x + width} exceeds "
                f"Analysis Image width {image_width}"
            )
        if y + height > image_height:
            errors.append(
                f"$.children[{index}].bbox: bottom edge {y + height} exceeds "
                f"Analysis Image height {image_height}"
            )
    return errors


def validate_document(data: Any, analysis_image: Path) -> list[str]:
    """Validate structure and real-image bounds without semantic judgment or mutation."""

    errors = validate_schema(data)
    children = data.get("children") if isinstance(data, dict) else None
    errors.extend(_validate_child_ids(children))
    try:
        actual_size = read_image_size(analysis_image)
    except ValueError as exc:
        errors.append(f"$: {exc}")
        return errors
    errors.extend(_validate_bbox_bounds(children, actual_size))
    return errors


def validate_file(document: Path, analysis_image: Path) -> list[str]:
    try:
        data = load_json(document)
    except json.JSONDecodeError as exc:
        return [
            f"$: invalid JSON in {document}: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ]
    except (OSError, UnicodeError) as exc:
        return [f"$: unable to read {document}: {exc}"]
    return validate_document(data, analysis_image)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Stage2-A structural_split v0.1 JSON."
    )
    parser.add_argument("document", type=Path, help="structural-split.json")
    parser.add_argument(
        "--analysis-image",
        required=True,
        type=Path,
        help="actual deterministic Analysis Image used by the VLM",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_file(args.document, args.analysis_image)
    if errors:
        print(f"Validation failed for {args.document}:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Valid structural split v0.1: {args.document}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
