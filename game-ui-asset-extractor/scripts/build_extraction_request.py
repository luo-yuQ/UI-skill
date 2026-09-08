"""Build extraction-request.json from reviewed-direct-assets.json.

Phase 5 (E2E contract freeze) minimal deterministic producer, frozen v0.1.

Contract bridge:
    Stage2-A reviewed-direct-assets.json  (direct-assets-reviewed-v0.1)
        -> deterministic mapping
    Stage2-B extraction-request.json      (extraction-request v0.1)

Frozen mapping rules (v0.1):
    asset_id        <- reviewed asset["id"]        (validated, never rewritten)
    asset_type      <- reviewed asset["taxonomy"]  (if in enum, else "unknown")
    final_bbox      <- reviewed asset["bbox_source"] (authoritative, byte-for-byte)
    extraction_mode <- fixed default "direct_crop" (CLI --extraction-mode override)

Hard boundaries (frozen v0.1):
    - No VLM calls.
    - No bbox arithmetic, no bbox repair, no re-discovery, no re-crop.
    - No image I/O; this script only maps JSON to JSON.
    - Assets dropped during review are simply absent from the reviewed file
      and therefore never reach the extraction request.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REVIEWED_SCHEMA_VERSION = "direct-assets-reviewed-v0.1"
REQUEST_SCHEMA_VERSION = "0.1"

ASSET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

ASSET_TYPES = frozenset(
    {
        "background",
        "panel",
        "button",
        "icon",
        "illustration",
        "frame",
        "progress_bar",
        "decoration",
        "text",
        "unknown",
    }
)

EXTRACTION_MODES = ("direct_crop", "foreground_extract")
DEFAULT_EXTRACTION_MODE = "direct_crop"

UNKNOWN_ASSET_TYPE = "unknown"


class ExtractionRequestBuildError(ValueError):
    """Raised when the reviewed document cannot be mapped deterministically."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ExtractionRequestBuildError(message)


def build_extraction_request(
    reviewed_doc: dict[str, Any],
    *,
    extraction_mode: str = DEFAULT_EXTRACTION_MODE,
) -> dict[str, Any]:
    """Map a reviewed-direct-assets document to an extraction-request document.

    Pure function: no filesystem access, no image access, no network access.
    """
    _require(
        isinstance(reviewed_doc, dict),
        "reviewed document must be a JSON object",
    )
    _require(
        reviewed_doc.get("schema_version") == REVIEWED_SCHEMA_VERSION,
        "reviewed document schema_version must be "
        f"{REVIEWED_SCHEMA_VERSION!r}, got {reviewed_doc.get('schema_version')!r}",
    )
    _require(
        extraction_mode in EXTRACTION_MODES,
        f"extraction_mode must be one of {EXTRACTION_MODES}, got {extraction_mode!r}",
    )

    source_image = reviewed_doc.get("source_image")
    _require(
        isinstance(source_image, str) and source_image,
        "reviewed document source_image must be a non-empty string",
    )

    reviewed_assets = reviewed_doc.get("assets")
    _require(
        isinstance(reviewed_assets, list) and len(reviewed_assets) >= 1,
        "reviewed document must contain at least one asset",
    )

    mapped_assets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, asset in enumerate(reviewed_assets):
        where = f"assets[{index}]"
        _require(isinstance(asset, dict), f"{where} must be a JSON object")

        asset_id = asset.get("id")
        _require(
            isinstance(asset_id, str) and asset_id,
            f"{where}.id must be a non-empty string",
        )
        _require(
            bool(ASSET_ID_PATTERN.match(asset_id)),
            f"{where}.id {asset_id!r} does not match extraction-request "
            "asset_id pattern ^[a-z0-9][a-z0-9_-]*$; fix the id upstream "
            "(review overrides) instead of relying on the builder",
        )
        _require(
            asset_id not in seen_ids,
            f"{where}.id {asset_id!r} is duplicated in reviewed document",
        )
        seen_ids.add(asset_id)

        bbox_source = asset.get("bbox_source")
        _require(
            isinstance(bbox_source, dict),
            f"{where}.bbox_source is required (authoritative bbox)",
        )
        final_bbox = {
            "x": bbox_source["x"],
            "y": bbox_source["y"],
            "width": bbox_source["width"],
            "height": bbox_source["height"],
        }
        for key in ("x", "y", "width", "height"):
            value = final_bbox[key]
            _require(
                isinstance(value, int) and not isinstance(value, bool),
                f"{where}.bbox_source.{key} must be an integer",
            )
        _require(
            final_bbox["x"] >= 0 and final_bbox["y"] >= 0,
            f"{where}.bbox_source x/y must be >= 0",
        )
        _require(
            final_bbox["width"] >= 1 and final_bbox["height"] >= 1,
            f"{where}.bbox_source width/height must be >= 1",
        )

        taxonomy = asset.get("taxonomy")
        _require(
            isinstance(taxonomy, str) and taxonomy,
            f"{where}.taxonomy must be a non-empty string",
        )
        asset_type = taxonomy if taxonomy in ASSET_TYPES else UNKNOWN_ASSET_TYPE

        mapped_assets.append(
            {
                "asset_id": asset_id,
                "asset_type": asset_type,
                "final_bbox": final_bbox,
                "extraction_mode": extraction_mode,
            }
        )

    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "source_image": source_image,
        "assets": mapped_assets,
    }


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministically build extraction-request.json from "
            "reviewed-direct-assets.json (Stage2-A -> Stage2-B bridge, v0.1)."
        )
    )
    parser.add_argument(
        "--reviewed-json",
        required=True,
        help="Path to reviewed-direct-assets.json (direct-assets-reviewed-v0.1)",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Output path for extraction-request.json",
    )
    parser.add_argument(
        "--extraction-mode",
        choices=EXTRACTION_MODES,
        default=DEFAULT_EXTRACTION_MODE,
        help=(
            "Global extraction mode for every asset "
            f"(default: {DEFAULT_EXTRACTION_MODE})"
        ),
    )
    args = parser.parse_args(argv)

    reviewed_doc = load_json(Path(args.reviewed_json))
    request = build_extraction_request(
        reviewed_doc, extraction_mode=args.extraction_mode
    )

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(request, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(
        f"built {output_path} with {len(request['assets'])} asset(s) "
        f"(extraction_mode={args.extraction_mode})"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ExtractionRequestBuildError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
