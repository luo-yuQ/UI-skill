#!/usr/bin/env python3
"""Apply Human Review deltas to immutable Direct Asset Discovery output."""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from pathlib import Path


REVIEWED_SCHEMA_VERSION = "direct-assets-reviewed-v0.1"
OVERRIDES_SCHEMA_VERSION = "direct-asset-review-overrides-v0.1"
BBOX_KEYS = ("x", "y", "width", "height")
VALID_DECISIONS = {"KEEP", "DROP"}


def load_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {description} '{path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {description} '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} root must be a JSON object")
    return value


def require_image_size(value, field: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    width, height = value.get("width"), value.get("height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ValueError(f"{field}.width and .height must be positive integers")
    return width, height


def validate_bbox(value, field: str, width: int, height: int) -> dict:
    if not isinstance(value, dict) or set(value) != set(BBOX_KEYS):
        raise ValueError(f"{field} must contain exactly x/y/width/height")
    for key in BBOX_KEYS:
        if isinstance(value[key], bool) or not isinstance(value[key], int):
            raise ValueError(f"{field}.{key} must be an integer")
    if value["x"] < 0 or value["y"] < 0:
        raise ValueError(f"{field}.x and .y must be >= 0")
    if value["width"] <= 0 or value["height"] <= 0:
        raise ValueError(f"{field}.width and .height must be > 0")
    if value["x"] + value["width"] > width or value["y"] + value["height"] > height:
        raise ValueError(f"{field} lies outside source image bounds {width}x{height}")
    return {key: value[key] for key in BBOX_KEYS}


def validate_decision(value, field: str) -> str:
    if value not in VALID_DECISIONS:
        raise ValueError(f"{field} must be KEEP or DROP")
    return value


def apply_review(assets_doc: dict, overrides_doc: dict, assets_name: str, overrides_name: str) -> dict:
    source_width, source_height = require_image_size(
        assets_doc.get("source_image_size"), "direct-assets.json.source_image_size"
    )
    review_width, review_height = require_image_size(
        overrides_doc.get("image_size"), "review-overrides.json.image_size"
    )
    if (review_width, review_height) != (source_width, source_height):
        raise ValueError(
            "review-overrides.json.image_size does not match "
            "direct-assets.json.source_image_size; bbox scaling is forbidden"
        )

    source_assets_json = overrides_doc.get("source_assets_json")
    if not isinstance(source_assets_json, str) or Path(source_assets_json).name != Path(assets_name).name:
        raise ValueError(
            "review-overrides.json.source_assets_json does not match --assets-json basename"
        )
    if overrides_doc.get("schema_version") != OVERRIDES_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported review-overrides.json schema_version: "
            f"{overrides_doc.get('schema_version')!r}"
        )

    original_assets = assets_doc.get("assets")
    if not isinstance(original_assets, list):
        raise ValueError("direct-assets.json.assets must be an array")
    original_by_id = {}
    for index, asset in enumerate(original_assets):
        field = f"direct-assets.json.assets[{index}]"
        if not isinstance(asset, dict):
            raise ValueError(f"{field} must be an object")
        asset_id = asset.get("id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError(f"{field}.id must be a non-empty string")
        if asset_id in original_by_id:
            raise ValueError(f"duplicate original asset id: {asset_id}")
        validate_bbox(asset.get("bbox_source"), f"{field}.bbox_source", source_width, source_height)
        original_by_id[asset_id] = asset

    overrides = overrides_doc.get("overrides")
    manual_assets = overrides_doc.get("manual_assets")
    if not isinstance(overrides, dict):
        raise ValueError("review-overrides.json.overrides must be an object")
    if not isinstance(manual_assets, list):
        raise ValueError("review-overrides.json.manual_assets must be an array")

    unknown_ids = sorted(set(overrides) - set(original_by_id))
    if unknown_ids:
        raise ValueError("override references unknown asset id(s): " + ", ".join(unknown_ids))

    validated_overrides = {}
    for asset_id, override in overrides.items():
        field = f"review-overrides.json.overrides[{asset_id!r}]"
        if not isinstance(override, dict) or not override:
            raise ValueError(f"{field} must be a non-empty object")
        unknown_fields = set(override) - {"bbox", "decision"}
        if unknown_fields:
            raise ValueError(f"{field} has unknown fields: {sorted(unknown_fields)}")
        validated = {}
        if "bbox" in override:
            validated["bbox"] = validate_bbox(
                override["bbox"], f"{field}.bbox", source_width, source_height
            )
        if "decision" in override:
            validated["decision"] = validate_decision(override["decision"], f"{field}.decision")
        validated_overrides[asset_id] = validated

    final_assets = []
    dropped_ids = []
    bbox_modified_count = 0
    explicit_keep_count = 0
    for original in original_assets:
        asset_id = original["id"]
        override = validated_overrides.get(asset_id)
        if override and override.get("decision") == "DROP":
            dropped_ids.append(asset_id)
            continue
        effective = copy.deepcopy(original)
        if override:
            has_bbox = "bbox" in override
            is_keep = override.get("decision") == "KEEP"
            if has_bbox:
                original_bbox = copy.deepcopy(effective["bbox_source"])
                effective["bbox_source"] = copy.deepcopy(override["bbox"])
                bbox_modified_count += 1
            if is_keep:
                explicit_keep_count += 1
            if has_bbox or is_keep:
                if has_bbox and is_keep:
                    review_status = "bbox_modified_and_kept"
                elif has_bbox:
                    review_status = "bbox_modified"
                else:
                    review_status = "explicit_keep"
                effective["review"] = {"status": review_status}
                if has_bbox:
                    effective["review"]["original_bbox_source"] = original_bbox
        final_assets.append(effective)

    manual_ids = set()
    manual_kept_count = 0
    manual_dropped_ids = []
    for index, manual in enumerate(manual_assets):
        field = f"review-overrides.json.manual_assets[{index}]"
        if not isinstance(manual, dict):
            raise ValueError(f"{field} must be an object")
        unknown_fields = set(manual) - {"asset_ref", "bbox", "decision"}
        if unknown_fields:
            raise ValueError(f"{field} has unknown fields: {sorted(unknown_fields)}")
        asset_id = manual.get("asset_ref")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError(f"{field}.asset_ref must be a non-empty string")
        if asset_id in original_by_id:
            raise ValueError(f"manual asset id conflicts with original asset id: {asset_id}")
        if asset_id in manual_ids:
            raise ValueError(f"duplicate manual asset id: {asset_id}")
        manual_ids.add(asset_id)
        bbox = validate_bbox(manual.get("bbox"), f"{field}.bbox", source_width, source_height)
        decision = validate_decision(manual.get("decision", "KEEP"), f"{field}.decision")
        if decision == "DROP":
            manual_dropped_ids.append(asset_id)
            continue
        final_assets.append(
            {
                "id": asset_id,
                "label": asset_id,
                "taxonomy": "manual",
                "bbox_source": bbox,
                "review_origin": "manual",
            }
        )
        manual_kept_count += 1

    output = copy.deepcopy(assets_doc)
    output["schema_version"] = REVIEWED_SCHEMA_VERSION
    output["source_direct_assets_schema_version"] = assets_doc.get("schema_version")
    output["source_assets_json"] = Path(assets_name).name
    output["review_overrides_json"] = Path(overrides_name).name
    output["assets"] = final_assets
    output["review_summary"] = {
        "original_asset_count": len(original_assets),
        "bbox_modified_count": bbox_modified_count,
        "explicit_keep_count": explicit_keep_count,
        "dropped_count": len(dropped_ids),
        "manual_kept_count": manual_kept_count,
        "manual_dropped_count": len(manual_dropped_ids),
        "final_asset_count": len(final_assets),
        "dropped_asset_ids": dropped_ids,
        "manual_dropped_asset_ids": manual_dropped_ids,
    }
    return output


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Apply Direct Asset Human Review overrides")
    parser.add_argument("--assets-json", required=True)
    parser.add_argument("--overrides-json", required=True)
    parser.add_argument("--output-json", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    assets_path = Path(args.assets_json).resolve()
    overrides_path = Path(args.overrides_json).resolve()
    output_path = Path(args.output_json).resolve()
    if output_path in {assets_path, overrides_path}:
        raise SystemExit("ERROR: --output-json must not overwrite either input file")
    try:
        assets_doc = load_json(assets_path, "direct-assets.json")
        overrides_doc = load_json(overrides_path, "review-overrides.json")
        reviewed = apply_review(assets_doc, overrides_doc, assets_path.name, overrides_path.name)
        write_json_atomic(output_path, reviewed)
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    summary = reviewed["review_summary"]
    print(f"[apply-review] input assets    : {assets_path} ({summary['original_asset_count']})")
    print(f"[apply-review] input overrides : {overrides_path}")
    print(f"[apply-review] output           : {output_path}")
    print(
        "[apply-review] result           : "
        f"{summary['final_asset_count']} final, "
        f"{summary['dropped_count']} dropped, "
        f"{summary['bbox_modified_count']} bbox modified, "
        f"{summary['manual_kept_count']} manual kept"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
