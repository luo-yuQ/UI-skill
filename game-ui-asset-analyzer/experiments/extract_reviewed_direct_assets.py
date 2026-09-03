#!/usr/bin/env python3
"""Deterministically crop reviewed direct assets in source pixel space."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError


REVIEWED_SCHEMA_VERSION = "direct-assets-reviewed-v0.1"
MANIFEST_SCHEMA_VERSION = "reviewed-direct-asset-raw-extraction-v0.1"
BBOX_KEYS = ("x", "y", "width", "height")
SAFE_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read reviewed assets JSON '{path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid reviewed assets JSON '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("reviewed assets JSON root must be an object")
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
    x, y, crop_width, crop_height = (value[key] for key in BBOX_KEYS)
    if x < 0 or y < 0 or crop_width <= 0 or crop_height <= 0:
        raise ValueError(f"{field} must have x/y >= 0 and width/height > 0")
    if x + crop_width > width or y + crop_height > height:
        raise ValueError(f"{field} lies outside source image bounds {width}x{height}")
    return {key: value[key] for key in BBOX_KEYS}


def validate_assets(doc: dict, assets_path: Path, image_path: Path, actual_size: tuple[int, int]):
    if doc.get("schema_version") != REVIEWED_SCHEMA_VERSION:
        raise ValueError(f"unsupported reviewed schema_version: {doc.get('schema_version')!r}")
    declared_size = require_image_size(doc.get("source_image_size"), "source_image_size")
    if declared_size != actual_size:
        raise ValueError(
            f"actual image size {actual_size[0]}x{actual_size[1]} does not match "
            f"reviewed source_image_size {declared_size[0]}x{declared_size[1]}; "
            "resize/scale is forbidden"
        )
    source_image = doc.get("source_image")
    if not isinstance(source_image, str) or Path(source_image).name != image_path.name:
        raise ValueError("reviewed source_image does not match --image basename")
    assets = doc.get("assets")
    if not isinstance(assets, list):
        raise ValueError("assets must be an array")
    validated = []
    ids = set()
    output_names = set()
    for index, asset in enumerate(assets):
        field = f"assets[{index}]"
        if not isinstance(asset, dict):
            raise ValueError(f"{field} must be an object")
        asset_id = asset.get("id")
        if not isinstance(asset_id, str) or not SAFE_ASSET_ID.fullmatch(asset_id) or asset_id in {".", ".."}:
            raise ValueError(f"{field}.id is not a safe output filename component: {asset_id!r}")
        if asset_id in ids:
            raise ValueError(f"duplicate asset id: {asset_id}")
        ids.add(asset_id)
        output_file = asset_id + ".png"
        folded = output_file.casefold()
        if folded in output_names:
            raise ValueError(f"asset ids collide as output filenames: {output_file}")
        output_names.add(folded)
        bbox = validate_bbox(asset.get("bbox_source"), f"{field}.bbox_source", *actual_size)
        validated.append((asset_id, bbox, output_file))
    summary = doc.get("review_summary")
    if isinstance(summary, dict) and summary.get("final_asset_count") != len(validated):
        raise ValueError("review_summary.final_asset_count does not match assets length")
    return validated


def write_json_atomic(path: Path, value: dict) -> None:
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


def extract(image_path: Path, assets_path: Path, output_dir: Path) -> dict:
    doc = load_json(assets_path)
    try:
        with Image.open(image_path) as opened:
            opened.load()
            actual_size = opened.size
            validated = validate_assets(doc, assets_path, image_path, actual_size)
            output_dir.mkdir(parents=True, exist_ok=True)
            print(f"[extract-reviewed] output directory : {output_dir}")
            manifest_assets = []
            for asset_id, bbox, output_file in validated:
                output_path = output_dir / output_file
                if output_path.exists():
                    print(f"[extract-reviewed] overwriting       : {output_path}")
                x, y = bbox["x"], bbox["y"]
                crop_width, crop_height = bbox["width"], bbox["height"]
                crop = opened.crop((x, y, x + crop_width, y + crop_height))
                if crop.size != (crop_width, crop_height):
                    raise RuntimeError(f"unexpected crop size for {asset_id}: {crop.size}")
                fd, temp_name = tempfile.mkstemp(prefix=output_file + ".", suffix=".tmp", dir=output_dir)
                os.close(fd)
                try:
                    crop.save(temp_name, format="PNG")
                    os.replace(temp_name, output_path)
                except BaseException:
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass
                    raise
                manifest_assets.append(
                    {
                        "id": asset_id,
                        "bbox_source": bbox,
                        "output_file": output_file,
                        "output_size": {"width": crop_width, "height": crop_height},
                    }
                )
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"cannot read source image '{image_path}': {exc}") from exc

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_image": image_path.name,
        "source_size": {"width": actual_size[0], "height": actual_size[1]},
        "assets_json": assets_path.name,
        "asset_count": len(manifest_assets),
        "assets": manifest_assets,
    }
    write_json_atomic(output_dir / "extraction-manifest.json", manifest)
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Extract exact raw crops from reviewed direct assets")
    parser.add_argument("--image", required=True)
    parser.add_argument("--assets-json", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    image_path = Path(args.image).resolve()
    assets_path = Path(args.assets_json).resolve()
    output_dir = Path(args.output_dir).resolve()
    try:
        manifest = extract(image_path, assets_path, output_dir)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(f"[extract-reviewed] source image     : {image_path}")
    print(f"[extract-reviewed] reviewed assets  : {assets_path}")
    print(f"[extract-reviewed] crops written    : {manifest['asset_count']}")
    print(f"[extract-reviewed] manifest         : {output_dir / 'extraction-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
