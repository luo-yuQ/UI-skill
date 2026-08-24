#!/usr/bin/env python3
"""Validate raw or processed Stage2-A Level-1 region documents."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "level1-regions.schema.json"
RAW_REGION_KEYS = {"id", "node_kind", "label", "description", "bbox", "confidence"}
PROCESSED_REGION_KEYS = RAW_REGION_KEYS | {
    "analysis_bbox",
    "padding",
    "output_crop",
    "upscale",
    "transform",
}
PROCESSED_REGION_REQUIRED_KEYS = {
    "id",
    "node_kind",
    "label",
    "bbox",
    "analysis_bbox",
    "padding",
    "output_crop",
    "upscale",
    "transform",
}
RAW_TOP_LEVEL_KEYS = {
    "schema_version",
    "source_image",
    "source_size",
    "background_root",
    "regions",
}
PROCESSED_TOP_LEVEL_KEYS = RAW_TOP_LEVEL_KEYS | {"processing"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_image_size(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise ValueError(f"source image does not exist or is not a file: {path}")
    try:
        with Image.open(path) as image:
            size = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read image {path}: {exc}") from exc
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError(f"image has invalid dimensions: {size[0]}x{size[1]}")
    return size


def _json_path(parts: Iterable[Any]) -> str:
    result = "$"
    for part in parts:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result


def _raw_projection(data: Any) -> Any:
    """Remove deterministic fields so the VLM part can use the strict raw schema."""

    if not isinstance(data, dict):
        return data
    result = {key: value for key, value in data.items() if key in RAW_TOP_LEVEL_KEYS}
    regions = result.get("regions")
    if isinstance(regions, list):
        result["regions"] = [
            {key: value for key, value in region.items() if key in RAW_REGION_KEYS}
            if isinstance(region, dict)
            else region
            for region in regions
        ]
    return result


def validate_raw(data: Any, source_image: Path | None = None) -> list[str]:
    errors: list[str] = []
    try:
        schema = load_json(SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"$: unable to load schema {SCHEMA_PATH}: {exc}"]

    validator = Draft202012Validator(schema)
    errors.extend(
        f"{_json_path(error.absolute_path)}: {error.message}"
        for error in sorted(
            validator.iter_errors(data),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                item.message,
            ),
        )
    )
    if not isinstance(data, dict):
        return errors

    regions = data.get("regions")
    if isinstance(regions, list):
        ids = [
            region.get("id")
            for region in regions
            if isinstance(region, dict) and isinstance(region.get("id"), str)
        ]
        for region_id, count in Counter(ids).items():
            if count > 1:
                errors.append(f"$.regions: duplicate region id {region_id!r}")

    source_size = data.get("source_size")
    if (
        isinstance(source_size, dict)
        and type(source_size.get("width")) is int
        and type(source_size.get("height")) is int
        and isinstance(regions, list)
    ):
        image_width = source_size["width"]
        image_height = source_size["height"]
        for index, region in enumerate(regions):
            bbox = region.get("bbox") if isinstance(region, dict) else None
            if not isinstance(bbox, dict):
                continue
            values = [bbox.get(key) for key in ("x", "y", "width", "height")]
            if not all(type(value) is int for value in values):
                continue
            x, y, width, height = values
            if x >= 0 and width > 0 and x + width > image_width:
                errors.append(
                    f"$.regions[{index}].bbox: right edge {x + width} exceeds source width {image_width}"
                )
            if y >= 0 and height > 0 and y + height > image_height:
                errors.append(
                    f"$.regions[{index}].bbox: bottom edge {y + height} exceeds source height {image_height}"
                )

    if source_image is not None:
        try:
            actual_size = read_image_size(source_image)
        except ValueError as exc:
            errors.append(f"$: {exc}")
        else:
            if isinstance(source_size, dict):
                declared_size = (source_size.get("width"), source_size.get("height"))
                if declared_size != actual_size:
                    errors.append(
                        "$.source_size: declared "
                        f"{declared_size[0]}x{declared_size[1]} does not match image "
                        f"{actual_size[0]}x{actual_size[1]}"
                    )
    return errors


def _is_finite_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and (value > 0 if positive else True)


def _check_exact_keys(value: Any, expected: set[str], path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path}: must be an object"]
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    errors = [f"{path}: missing required field {key!r}" for key in missing]
    errors.extend(f"{path}: unexpected field {key!r}" for key in extra)
    return errors


def _check_allowed_keys(
    value: Any, required: set[str], allowed: set[str], path: str
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path}: must be an object"]
    missing = sorted(required - set(value))
    extra = sorted(set(value) - allowed)
    errors = [f"{path}: missing required field {key!r}" for key in missing]
    errors.extend(f"{path}: unexpected field {key!r}" for key in extra)
    return errors


def _validate_bbox_in_source(
    bbox: Any, source_size: tuple[int, int], path: str
) -> list[str]:
    if not isinstance(bbox, dict):
        return [f"{path}: must be an object"]
    if set(bbox) != {"x", "y", "width", "height"}:
        return [f"{path}: must contain exactly x, y, width, height"]
    if not all(type(bbox.get(key)) is int for key in bbox):
        return [f"{path}: all values must be integers"]
    x, y, width, height = (bbox[key] for key in ("x", "y", "width", "height"))
    errors: list[str] = []
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        errors.append(f"{path}: origin must be non-negative and size must be positive")
    if x + width > source_size[0] or y + height > source_size[1]:
        errors.append(f"{path}: bbox exceeds source image bounds")
    return errors


def validate_processed(
    data: Any,
    document_path: Path | None = None,
    source_image: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["$: must be an object"]
    errors.extend(_check_exact_keys(data, PROCESSED_TOP_LEVEL_KEYS, "$"))
    errors.extend(validate_raw(_raw_projection(data), source_image))

    source_size_value = data.get("source_size")
    if not (
        isinstance(source_size_value, dict)
        and type(source_size_value.get("width")) is int
        and type(source_size_value.get("height")) is int
    ):
        return errors
    source_size = (source_size_value["width"], source_size_value["height"])

    processing = data.get("processing")
    processing_keys = {"padding_ratio", "min_output_short_side", "max_upscale"}
    errors.extend(_check_exact_keys(processing, processing_keys, "$.processing"))
    if isinstance(processing, dict):
        padding_ratio = processing.get("padding_ratio")
        min_short = processing.get("min_output_short_side")
        max_upscale = processing.get("max_upscale")
        if not _is_finite_number(padding_ratio) or padding_ratio < 0:
            errors.append("$.processing.padding_ratio: must be a finite number >= 0")
        if type(min_short) is not int or min_short < 0:
            errors.append("$.processing.min_output_short_side: must be an integer >= 0")
        if not _is_finite_number(max_upscale) or max_upscale < 1:
            errors.append("$.processing.max_upscale: must be a finite number >= 1")

    regions = data.get("regions")
    if not isinstance(regions, list):
        return errors
    for index, region in enumerate(regions):
        path = f"$.regions[{index}]"
        errors.extend(
            _check_allowed_keys(
                region,
                PROCESSED_REGION_REQUIRED_KEYS,
                PROCESSED_REGION_KEYS,
                path,
            )
        )
        if not isinstance(region, dict):
            continue
        bbox = region.get("bbox")
        analysis_bbox = region.get("analysis_bbox")
        errors.extend(_validate_bbox_in_source(bbox, source_size, f"{path}.bbox"))
        errors.extend(
            _validate_bbox_in_source(analysis_bbox, source_size, f"{path}.analysis_bbox")
        )
        if not isinstance(bbox, dict) or not isinstance(analysis_bbox, dict):
            continue

        padding = region.get("padding")
        padding_keys = {"ratio", "requested_pixels", "actual_pixels"}
        errors.extend(_check_exact_keys(padding, padding_keys, f"{path}.padding"))
        if isinstance(padding, dict):
            requested = padding.get("requested_pixels")
            actual = padding.get("actual_pixels")
            errors.extend(
                _check_exact_keys(requested, {"x", "y"}, f"{path}.padding.requested_pixels")
            )
            errors.extend(
                _check_exact_keys(
                    actual,
                    {"left", "top", "right", "bottom"},
                    f"{path}.padding.actual_pixels",
                )
            )
            if isinstance(actual, dict) and all(
                type(actual.get(key)) is int for key in ("left", "top", "right", "bottom")
            ):
                expected_actual = {
                    "left": bbox["x"] - analysis_bbox["x"],
                    "top": bbox["y"] - analysis_bbox["y"],
                    "right": analysis_bbox["x"] + analysis_bbox["width"] - bbox["x"] - bbox["width"],
                    "bottom": analysis_bbox["y"] + analysis_bbox["height"] - bbox["y"] - bbox["height"],
                }
                if actual != expected_actual:
                    errors.append(f"{path}.padding.actual_pixels: inconsistent with bboxes")

        transform = region.get("transform")
        transform_keys = {
            "source_x",
            "source_y",
            "source_width",
            "source_height",
            "output_width",
            "output_height",
            "scale_x",
            "scale_y",
        }
        errors.extend(_check_exact_keys(transform, transform_keys, f"{path}.transform"))
        if isinstance(transform, dict) and all(key in transform for key in transform_keys):
            integer_keys = {
                "source_x",
                "source_y",
                "source_width",
                "source_height",
                "output_width",
                "output_height",
            }
            if not all(type(transform[key]) is int for key in integer_keys):
                errors.append(f"{path}.transform: coordinate and size fields must be integers")
            elif (
                transform["source_x"] != analysis_bbox["x"]
                or transform["source_y"] != analysis_bbox["y"]
                or transform["source_width"] != analysis_bbox["width"]
                or transform["source_height"] != analysis_bbox["height"]
                or transform["source_width"] <= 0
                or transform["source_height"] <= 0
                or transform["output_width"] <= 0
                or transform["output_height"] <= 0
            ):
                errors.append(f"{path}.transform: invalid or inconsistent source/output geometry")
            for scale_key, output_key, source_key in (
                ("scale_x", "output_width", "source_width"),
                ("scale_y", "output_height", "source_height"),
            ):
                scale = transform.get(scale_key)
                if not _is_finite_number(scale, positive=True):
                    errors.append(f"{path}.transform.{scale_key}: must be finite and > 0")
                elif type(transform.get(output_key)) is int and type(transform.get(source_key)) is int:
                    expected_scale = transform[output_key] / transform[source_key]
                    if not math.isclose(scale, expected_scale, rel_tol=1e-9, abs_tol=1e-9):
                        errors.append(f"{path}.transform.{scale_key}: inconsistent with dimensions")

        upscale = region.get("upscale")
        upscale_keys = {"applied", "scale_x", "scale_y"}
        errors.extend(_check_exact_keys(upscale, upscale_keys, f"{path}.upscale"))
        if isinstance(upscale, dict):
            if type(upscale.get("applied")) is not bool:
                errors.append(f"{path}.upscale.applied: must be boolean")
            if isinstance(transform, dict):
                for key in ("scale_x", "scale_y"):
                    if upscale.get(key) != transform.get(key):
                        errors.append(f"{path}.upscale.{key}: inconsistent with transform")

        crop_path_value = region.get("output_crop")
        if not isinstance(crop_path_value, str) or not crop_path_value:
            errors.append(f"{path}.output_crop: must be a non-empty string")
        elif document_path is not None:
            crop_path = Path(crop_path_value)
            if not crop_path.is_absolute():
                crop_path = document_path.parent / crop_path
            try:
                crop_size = read_image_size(crop_path)
            except ValueError as exc:
                errors.append(f"{path}.output_crop: {exc}")
            else:
                if isinstance(transform, dict):
                    expected_size = (transform.get("output_width"), transform.get("output_height"))
                    if crop_size != expected_size:
                        errors.append(
                            f"{path}.output_crop: image size {crop_size[0]}x{crop_size[1]} "
                            f"does not match transform {expected_size[0]}x{expected_size[1]}"
                        )
    return errors


def validate_document(
    data: Any,
    document_path: Path | None = None,
    source_image: Path | None = None,
) -> tuple[str, list[str]]:
    regions = data.get("regions") if isinstance(data, dict) else None
    processed = isinstance(data, dict) and (
        "processing" in data
        or isinstance(regions, list)
        and any(isinstance(region, dict) and "analysis_bbox" in region for region in regions)
    )
    if processed:
        return "processed Level-1 regions", validate_processed(data, document_path, source_image)
    return "raw Level-1 regions", validate_raw(data, source_image)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate raw or processed Level-1 regions JSON.")
    parser.add_argument("document", type=Path)
    parser.add_argument("--source-image", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load_json(args.document)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"Validation failed: unable to read {args.document}: {exc}", file=sys.stderr)
        return 1
    kind, errors = validate_document(data, args.document, args.source_image)
    if errors:
        print("Validation failed:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 1
    print(f"Valid {kind}: {args.document}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
