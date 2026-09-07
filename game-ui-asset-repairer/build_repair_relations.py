#!/usr/bin/env python3
"""C1 — Repair Relation Builder (deterministic, v0.1).

Reads the reviewed direct-assets JSON and emits ``repair-relations.json``
describing which reviewed asset bboxes are strictly contained in another
reviewed asset bbox.

Contract (v0.1, frozen):

- strict bbox containment only: ``intersection_area == small_bbox_area``;
- no heuristic thresholds, no label/taxonomy reasoning, no VLM, no tree;
- large bbox  -> repair target / base;
- small bbox  -> occluder;
- multiple contained assets aggregate into one relation per target;
- the reviewed JSON is never modified; relations go to a new file only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from repair_geometry import (
    bbox_area,
    bbox_is_valid,
    contains_strict,
    intersection_area,
    require_valid_bbox,
)

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schemas" / "repair-relations.schema.json"
SCHEMA_VERSION = "repair-relations-v0.1"
RELATION_TYPE = "reviewed_bbox_containment"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_relations(document: dict[str, Any]) -> list[str]:
    schema = load_json(SCHEMA_PATH)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    return [
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors
    ]


def _asset_bbox_source(asset: dict[str, Any]) -> dict[str, Any]:
    """Reviewed JSON contract: bbox_source is authoritative.

    Falls back to final_bbox only for reviewed JSONs that already normalized
    that field name; bbox_analysis is never used.
    """

    for key in ("bbox_source", "final_bbox"):
        if key in asset:
            return asset[key]
    raise KeyError(
        f"asset {asset.get('id')!r} has no bbox_source / final_bbox"
    )


def build_relations(reviewed: dict[str, Any]) -> dict[str, Any]:
    assets = reviewed.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("reviewed JSON has no assets array")

    entries: list[tuple[str, dict[str, Any]]] = []
    for asset in assets:
        asset_id = asset.get("id")
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError(f"reviewed asset without id: {asset!r}")
        bbox = _asset_bbox_source(asset)
        require_valid_bbox(bbox, f"bbox_source of {asset_id}")
        entries.append((asset_id, bbox))

    occluders_by_target: dict[str, list[str]] = {}
    geometry_by_pair: dict[tuple[str, str], dict[str, Any]] = {}

    for target_id, target_bbox in entries:
        for occluder_id, occluder_bbox in entries:
            if target_id == occluder_id:
                continue
            if not contains_strict(target_bbox, occluder_bbox):
                continue
            occluders_by_target.setdefault(target_id, []).append(occluder_id)
            geometry_by_pair[(target_id, occluder_id)] = {
                "target_bbox_source": dict(target_bbox),
                "occluder_bbox_source": dict(occluder_bbox),
                "intersection_area": intersection_area(target_bbox, occluder_bbox),
                "occluder_bbox_area": bbox_area(occluder_bbox),
                "occluder_containment_ratio": (
                    intersection_area(target_bbox, occluder_bbox) / bbox_area(occluder_bbox)
                ),
            }

    relations: list[dict[str, Any]] = []
    for target_id, occluder_ids in occluders_by_target.items():
        occluder_ids = sorted(set(occluder_ids))
        first = geometry_by_pair[(target_id, occluder_ids[0])]
        relations.append(
            {
                "target_asset_id": target_id,
                "occluder_asset_ids": occluder_ids,
                "relation": RELATION_TYPE,
                "geometry": {
                    "target_bbox_source": first["target_bbox_source"],
                    "occluder_bbox_source": first["occluder_bbox_source"],
                    "intersection_area": first["intersection_area"],
                    "occluder_bbox_area": first["occluder_bbox_area"],
                    "occluder_containment_ratio": first["occluder_containment_ratio"],
                    "per_occluder": [geometry_by_pair[(target_id, oid)] for oid in occluder_ids],
                },
            }
        )

    relations.sort(key=lambda r: r["target_asset_id"])

    return {
        "schema_version": SCHEMA_VERSION,
        "source_image": reviewed.get("source_image", ""),
        "relations": relations,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C1: build repair relations from reviewed assets")
    parser.add_argument("--reviewed", required=True, help="Path to reviewed-direct-assets.json")
    parser.add_argument("--output", required=True, help="Path to repair-relations.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reviewed = load_json(Path(args.reviewed))
        document = build_relations(reviewed)
        errors = validate_relations(document)
        if errors:
            raise RuntimeError("Generated relations are invalid:\n" + "\n".join(errors))
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "output": str(args.output),
                "relation_count": len(document["relations"]),
                "targets": [r["target_asset_id"] for r in document["relations"]],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
