#!/usr/bin/env python3
"""Stage2 UI Recomposition v0.1 PoC (deterministic, no model calls).

Pipeline:
  clean repaired background (background-repaired.png)
  + reviewed-direct-assets.json (final human-reviewed bbox_source)
  + Stage2-B transparent component PNGs (filtered-rgba.png per asset)
      -> alpha-composite each component at bbox_source.x/y on the source
         coordinate canvas
      -> recomposed-preview.png + ui-compose.json + compose-report.json

Hard rules for this PoC:
  - Canvas = original source pixel coordinate (never analysis/display/
    crop-local/provider coords).
  - NO resize of components: size mismatch against bbox is recorded and the
    asset is NOT composed (silent stretching is forbidden).
  - Out-of-bounds bboxes are recorded, never silently clipped.
  - NO VLM / OCR / SAM / Image2 / any generative model.
  - Upstream inputs (reviewed JSON, asset PNGs, background) are read-only.
  - z-order: no real z field exists in the reviewed contract, so this PoC
    draws in reviewed asset list order and records
    "z_order_source": "reviewed_asset_order" (fallback, not true semantics).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image

PLAN_SCHEMA_VERSION = "ui-compose-v0.1"
COMPONENT_FILENAME = "filtered-rgba.png"
EXCLUDED_TAXONOMIES = {"background", "panel"}  # structural owners, not terminal assets


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def clamp_check_bbox(bbox: dict[str, Any], width: int, height: int
                     ) -> tuple[int, int, int, int, bool]:
    x = int(bbox["x"]); y = int(bbox["y"])
    w = int(bbox["width"]); h = int(bbox["height"])
    in_bounds = x >= 0 and y >= 0 and x + w <= width and y + h <= height
    return x, y, w, h, in_bounds


def build_components(doc: dict[str, Any], assets_root: Path,
                     src_w: int, src_h: int, exclude_ids: set[str]
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                                list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition reviewed assets into compose / excluded / missing / mismatch."""
    components: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []

    for asset in doc.get("assets", []):
        aid = str(asset.get("id"))
        taxonomy = str(asset.get("taxonomy", ""))
        bbox = asset.get("bbox_source")
        if not isinstance(bbox, dict):
            excluded.append({"asset_id": aid, "reason": "missing_bbox_source"})
            continue
        if aid in exclude_ids:
            excluded.append({"asset_id": aid, "reason": "explicit_exclusion"})
            continue
        if taxonomy in EXCLUDED_TAXONOMIES:
            excluded.append({"asset_id": aid, "reason": f"taxonomy_{taxonomy}"})
            continue

        x, y, w, h, in_bounds = clamp_check_bbox(bbox, src_w, src_h)
        if not in_bounds:
            excluded.append({"asset_id": aid, "reason": "out_of_bounds",
                             "bbox_source": {"x": x, "y": y, "width": w, "height": h}})
            continue

        png_path = assets_root / aid / COMPONENT_FILENAME
        if not png_path.is_file():
            missing.append({"asset_id": aid,
                            "expected_path": str(png_path),
                            "note": "filtered-rgba.png absent (not silently substituted)"})
            continue

        comp = Image.open(png_path)
        comp.load()
        if (comp.width, comp.height) != (w, h):
            mismatches.append({"asset_id": aid,
                               "bbox_size": [w, h],
                               "component_size": [comp.width, comp.height],
                               "status": "size_mismatch"})
            continue

        components.append({
            "asset_id": aid,
            "label": asset.get("label"),
            "taxonomy": taxonomy,
            "path": str(png_path),
            "bbox_source": {"x": x, "y": y, "width": w, "height": h},
            "x": x, "y": y, "width": w, "height": h,
            "z_index": len(components),
            "visible": True,
            "size_validation": "pass",
        })

    return components, excluded, missing, mismatches


def count_overlaps(components: list[dict[str, Any]]) -> list[list[str]]:
    """Cheap pairwise bbox intersection count (report info only)."""
    overlaps: list[list[str]] = []
    for i in range(len(components)):
        for j in range(i + 1, len(components)):
            a, b = components[i], components[j]
            if (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
                    and a["y"] < b["y"] + b["height"]
                    and b["y"] < a["y"] + a["height"]):
                overlaps.append([a["asset_id"], b["asset_id"]])
    return overlaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage2 UI Recomposition v0.1 PoC (deterministic)")
    parser.add_argument("--reviewed-assets", required=True)
    parser.add_argument("--assets-root", required=True,
                        help="Stage2-B extraction root (<asset_id>/filtered-rgba.png)")
    parser.add_argument("--background", required=True,
                        help="repaired clean background image")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--exclude-asset-id", action="append", default=[],
                        help="asset id to exclude; repeatable")
    parser.add_argument("--validate-only", action="store_true",
                        help="write ui-compose.json / compose-report.json, no preview")
    args = parser.parse_args(argv)

    reviewed_json = Path(args.reviewed_assets)
    assets_root = Path(args.assets_root)
    background_path = Path(args.background)
    out_dir = Path(args.out)
    exclude_ids = {a.strip() for a in args.exclude_asset_id if a.strip()}

    if not reviewed_json.is_file():
        raise SystemExit(f"ERROR: reviewed JSON not found: {reviewed_json}")
    if not background_path.is_file():
        raise SystemExit(f"ERROR: background not found: {background_path}")

    doc = load_json(reviewed_json)
    src_w = int(doc["source_image_size"]["width"])
    src_h = int(doc["source_image_size"]["height"])

    bg = Image.open(background_path)
    bg.load()
    bg_w, bg_h = bg.size
    size_aligned = (bg_w, bg_h) == (src_w, src_h)

    components, excluded, missing, mismatches = build_components(
        doc, assets_root, src_w, src_h, exclude_ids)
    overlaps = count_overlaps(components)

    status = "success"
    gaps: list[str] = []
    if not size_aligned:
        # Hard blocker: composing onto a misaligned background is forbidden.
        status = "validation_failure"
        gaps.append(f"BACKGROUND_SIZE != SOURCE_SIZE "
                    f"({bg_w}x{bg_h} vs {src_w}x{src_h})")
    if missing:
        # Recorded, NOT silently substituted; these assets are skipped.
        gaps.append(f"missing component PNGs: "
                    f"{[m['asset_id'] for m in missing]}")
    if mismatches:
        gaps.append(f"size mismatches: "
                    f"{[m['asset_id'] for m in mismatches]}")
    if status == "success" and gaps:
        status = "success_with_gaps"
    blocking = gaps if status == "validation_failure" else []

    out_dir.mkdir(parents=True, exist_ok=True)
    preview_rel = "recomposed-preview.png"

    compose = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": status,
        "validation_blockers": blocking,
        "validation_gaps": gaps,
        "source_size": {"width": src_w, "height": src_h},
        "background": {"path": str(background_path),
                       "width": bg_w, "height": bg_h,
                       "size_aligned_with_source": size_aligned},
        "z_order_source": "reviewed_asset_order",
        "components": components,
        "excluded_assets": excluded,
        "missing_assets": missing,
        "size_mismatches": mismatches,
        "output_image": preview_rel,
    }
    (out_dir / "ui-compose.json").write_text(
        json.dumps(compose, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "schema_version": "ui-compose-report-v0.1",
        "status": status,
        "reviewed_asset_count": len(doc.get("assets", [])),
        "composed_asset_count": len(components),
        "excluded_count": len(excluded),
        "missing_png_count": len(missing),
        "size_mismatch_count": len(mismatches),
        "out_of_bounds_count": sum(1 for e in excluded
                                   if e["reason"] == "out_of_bounds"),
        "overlapping_component_pairs": overlaps,
        "overlap_pair_count": len(overlaps),
        "canvas_size": {"width": src_w, "height": src_h},
        "background_size": {"width": bg_w, "height": bg_h},
        "source_image": doc.get("source_image"),
        "output_path": str(out_dir / preview_rel),
    }
    (out_dir / "compose-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"status: {status}")
    for b in blocking:
        print(f"  blocker: {b}")
    for g in gaps:
        print(f"  gap: {g}")
    print(f"reviewed={report['reviewed_asset_count']} "
          f"composed={report['composed_asset_count']} "
          f"excluded={report['excluded_count']} "
          f"missing={report['missing_png_count']} "
          f"mismatch={report['size_mismatch_count']} "
          f"oob={report['out_of_bounds_count']} "
          f"overlap_pairs={report['overlap_pair_count']}")
    print(f"compose ids: {[c['asset_id'] for c in components]}")
    print(f"excluded: {[(e['asset_id'], e['reason']) for e in excluded]}")
    if missing:
        print(f"missing: {[(m['asset_id'], m['expected_path']) for m in missing]}")

    if args.validate_only:
        print("VALIDATE-ONLY: ui-compose.json / compose-report.json written, "
              "no preview generated.")
        return 0 if status == "success" else 1

    if status == "validation_failure":
        print("COMPOSE ABORTED: validation failed (see blockers above). "
              "No preview generated.")
        return 1
    if gaps:
        print("NOTICE: composing with gaps — missing/mismatched assets are "
              "skipped, not silently substituted.")

    canvas = bg.convert("RGBA").copy()
    for comp in components:
        layer = Image.open(comp["path"]).convert("RGBA")
        canvas.alpha_composite(layer, dest=(comp["x"], comp["y"]))
    preview_path = out_dir / preview_rel
    canvas.convert("RGB").save(preview_path)
    print(f"DONE: {preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
