#!/usr/bin/env python3
"""C1.5 — Repair Input Preparation (deterministic, v0.2).

For each relation in ``repair-relations.json``:

1. load the target's extracted RGBA (its current visible state — it may
   still contain occluder pixels, which is expected);
2. load each occluder's Stage2-B final postprocessed segmentation mask
   (never re-run SAM, never use the bbox rectangle as repair mask);
3. deterministically project every occluder mask into the target's local
   frame via source-pixel arithmetic:
       source_x = occluder_frame.x + occluder_local_x
       target_local_x = source_x - target_frame.x      (Y symmetric)
4. repair_mask = union(projected occluder masks), clipped only by the
   target frame boundary. Since v0.2 the target's own segmentation mask is
   NOT a repair ownership boundary: an occluder known (via the reviewed
   bbox containment relation) to cover the target owns its full projected
   area inside the target frame;
5. write binary PNG masks: WHITE = must repair, BLACK = preserve;
   no soft alpha, no blur, dilation = 0;
6. write a working image copied from the target RGBA with RGB set to pure
   black (0,0,0) inside the repair mask — a missing-content placeholder
   only — leaving every other pixel byte-identical to the Stage2-B output.

This stage stops at repair-ready inputs. No repair algorithm runs here.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError

from repair_geometry import (
    bbox_edges,
    frame_origin,
    require_valid_bbox,
    target_frame_from_extraction_record,
)

ROOT = Path(__file__).resolve().parent
INPUT_SCHEMA_PATH = ROOT / "schemas" / "repair-input.schema.json"
RESULT_SCHEMA_PATH = ROOT / "schemas" / "repair-preparation-result.schema.json"

INPUT_SCHEMA_VERSION = "repair-input-v0.2"
RESULT_SCHEMA_VERSION = "repair-preparation-result-v0.1"

REPAIR_MASK_DILATION = 0  # v0.1 contract: no dilation

REASON_OK = "ok"


class SkipTarget(Exception):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validation_errors(document: Any, schema_path: Path) -> list[str]:
    validator = Draft202012Validator(load_json(schema_path))
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    return [
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors
    ]


def _read_text_safe(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def load_extraction_records(path: Path) -> dict[str, dict[str, Any]]:
    """Map asset_id -> extraction record from a Stage2-B extraction-result.json."""

    document = load_json(path)
    if not isinstance(document, dict) or not isinstance(document.get("assets"), list):
        raise ValueError(f"extraction result has no assets array: {path}")
    records: dict[str, dict[str, Any]] = {}
    for record in document["assets"]:
        asset_id = record.get("asset_id")
        if isinstance(asset_id, str):
            records[asset_id] = record
    return records


def resolve_relative(base: Path, relative: str | None) -> Path:
    if relative is None:
        raise FileNotFoundError("path field is null")
    candidate = Path(relative)
    return candidate if candidate.is_absolute() else base / candidate


def load_mask(path: Path, asset_id: str) -> np.ndarray:
    try:
        with Image.open(path) as image:
            mask = np.asarray(image.convert("L")).copy()
    except (FileNotFoundError, OSError, UnidentifiedImageError) as exc:
        raise SkipTarget("occluder_mask_file_missing" if "occluder" in asset_id else "target_mask_png_missing", str(exc)) from exc
    return mask > 127


def project_mask_to_target(
    occluder_mask: np.ndarray,
    occluder_frame: dict[str, int],
    target_frame: dict[str, int],
    target_shape: tuple[int, int],
) -> np.ndarray | None:
    """Deterministically project an occluder-local mask into target-local coords.

    source = occluder_frame origin + local coord
    target_local = source - target_frame origin
    Returns None when there is zero overlap (after clamping).
    """

    oh, ow = occluder_mask.shape
    th, tw = target_shape
    ofx, ofy = frame_origin(occluder_frame)
    tfx, tfy = frame_origin(target_frame)

    # occluder bbox in source pixels
    src_x1, src_y1 = ofx, ofy
    src_x2, src_y2 = ofx + ow, ofy + oh

    # overlap with target bbox in source pixels
    tx1, ty1 = tfx, tfy
    tx2, ty2 = tfx + tw, tfy + th

    ix1, iy1 = max(src_x1, tx1), max(src_y1, ty1)
    ix2, iy2 = min(src_x2, tx2), min(src_y2, ty2)
    if ix1 >= ix2 or iy1 >= iy2:
        return None

    # slice of the occluder mask that overlaps the target
    occl_x1, occl_y1 = ix1 - src_x1, iy1 - src_y1
    occl_x2, occl_y2 = ix2 - src_x1, iy2 - src_y1
    patch = occluder_mask[occl_y1:occl_y2, occl_x1:occl_x2]

    # where it lands in target-local coordinates
    dst_x1, dst_y1 = ix1 - tfx, iy1 - tfy
    dst_x2, dst_y2 = dst_x1 + patch.shape[1], dst_y1 + patch.shape[0]

    projected = np.zeros(target_shape, dtype=bool)
    projected[dst_y1:dst_y2, dst_x1:dst_x2] = patch
    return projected


def _asset_png_paths(record: dict[str, Any], result_base: Path) -> tuple[Path, Path]:
    asset_png = resolve_relative(result_base, record.get("output_path"))
    mask_png = resolve_relative(result_base, record.get("mask_path"))
    return asset_png, mask_png


def prepare_target(
    relation: dict[str, Any],
    extraction_records: dict[str, dict[str, Any]],
    extraction_result_path: Path,
    reviewed_assets: dict[str, dict[str, Any]],
    repair_dir: Path,
) -> dict[str, Any]:
    target_id = relation["target_asset_id"]
    occluder_ids = list(relation["occluder_asset_ids"])
    base: dict[str, Any] = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "status": "failed",
        "reason_code": None,
        "message": None,
        "target_asset_id": target_id,
        "occluder_asset_ids": occluder_ids,
        "target_bbox_source": None,
        "target_frame": None,
        "target_asset_path": None,
        "target_mask_path": None,
        "occluder_masks": [],
        "repair_mask_path": None,
        "repair_working_image_path": None,
        "repair_pixel_count": 0,
        "repair_ratio_of_target_mask": 0.0,
    }

    def finish(status: str, reason_code: str, message: str | None = None) -> dict[str, Any]:
        base["status"] = status
        base["reason_code"] = reason_code
        base["message"] = message
        return base

    try:
        target_record = extraction_records.get(target_id)
        if target_record is None:
            return finish("failed", "target_extraction_result_missing",
                          f"{target_id} not found in extraction-result.json")
        if target_record.get("status") != "success":
            return finish("failed", "target_extraction_failed",
                          f"{target_id} extraction status: {target_record.get('status')}")

        target_bbox = require_valid_bbox(
            deepcopy(target_record["final_bbox"]),
            f"{target_id} final_bbox",
        )
        target_frame = target_frame_from_extraction_record(target_record)
        base["target_bbox_source"] = deepcopy(target_bbox)
        base["target_frame"] = {**deepcopy(target_frame), "kind": "extraction_roi" if target_record.get("extraction_roi") is not None else "final_bbox"}

        target_asset_png, target_mask_png = _asset_png_paths(target_record, extraction_result_path.parent)
        if not target_asset_png.is_file():
            return finish("failed", "target_asset_png_missing", str(target_asset_png))
        if not target_mask_png.is_file():
            return finish("failed", "target_mask_png_missing", str(target_mask_png))
        base["target_asset_path"] = target_asset_png.as_posix()
        base["target_mask_path"] = target_mask_png.as_posix()

        with Image.open(target_asset_png) as image:
            target_rgba = np.asarray(image.convert("RGBA")).copy()
        with Image.open(target_mask_png) as image:
            target_mask = np.asarray(image.convert("L")).copy() > 127

        target_frame_shape = (int(target_frame["height"]), int(target_frame["width"]))
        if target_rgba.shape[:2] != target_frame_shape:
            return finish(
                "failed",
                "asset_image_size_mismatch",
                f"{target_id} asset PNG {target_rgba.shape[:2]} != frame {target_frame_shape}",
            )
        if target_mask.shape != target_frame_shape:
            return finish(
                "failed",
                "mask_size_mismatch",
                f"{target_id} mask {target_mask.shape} != frame {target_frame_shape}",
            )

        repair_mask = np.zeros(target_frame_shape, dtype=bool)
        projected_before_clip = 0
        occluder_entries: list[dict[str, Any]] = []
        for occluder_id in occluder_ids:
            occluder_record = extraction_records.get(occluder_id)
            if occluder_record is None:
                return finish("failed", "occluder_extraction_result_missing",
                              f"{occluder_id} not found in extraction-result.json")
            if occluder_record.get("status") != "success":
                return finish("failed", "occluder_extraction_failed",
                              f"{occluder_id} extraction status: {occluder_record.get('status')}")
            occluder_bbox = require_valid_bbox(
                deepcopy(occluder_record["final_bbox"]),
                f"{occluder_id} final_bbox",
            )
            occluder_frame = target_frame_from_extraction_record(occluder_record)
            _, occluder_mask_png = _asset_png_paths(occluder_record, extraction_result_path.parent)
            if not occluder_mask_png.is_file():
                return finish("failed", "occluder_mask_file_missing", str(occluder_mask_png))

            occluder_mask = load_mask(occluder_mask_png, occluder_id)
            if occluder_mask.shape != (int(occluder_frame["height"]), int(occluder_frame["width"])):
                return finish(
                    "failed",
                    "mask_size_mismatch",
                    f"{occluder_id} mask {occluder_mask.shape} != frame "
                    f"{(int(occluder_frame['height']), int(occluder_frame['width']))}",
                )

            projected = project_mask_to_target(
                occluder_mask,
                occluder_frame,
                target_frame,
                target_frame_shape,
            )
            if projected is None:
                return finish("failed", "no_coordinate_overlap",
                              f"{occluder_id} does not overlap {target_id} after mapping")

            repair_mask |= projected
            projected_before_clip += int(occluder_mask.sum())
            occluder_entries.append(
                {
                    "asset_id": occluder_id,
                    "bbox_source": deepcopy(occluder_bbox),
                    "mask_path": occluder_mask_png.as_posix(),
                }
            )

        base["occluder_masks"] = occluder_entries

        # Step 4 (v0.2 semantics): the repair mask is the union of all
        # projected occluder masks, clipped only by the target frame
        # boundary. The target's own segmentation mask is NOT a repair
        # ownership boundary anymore — an occluder known to cover the
        # target owns its full projected area inside the target frame.
        # The target mask is still loaded and size-checked above because
        # it remains recorded in the repair input metadata.

        # Step 5: binary mask, no dilation / blur.
        if not repair_mask.any():
            return finish("skipped", "empty_repair_mask",
                          "projected occluder masks are empty after frame clipping")

        target_asset_dir = repair_dir / target_id
        target_asset_dir.mkdir(parents=True, exist_ok=True)
        repair_mask_path = target_asset_dir / "repair-mask.png"
        working_image_path = target_asset_dir / "repair-working-image.png"

        Image.fromarray(repair_mask.astype(np.uint8) * 255, mode="L").save(
            repair_mask_path, format="PNG", optimize=True
        )

        # Step 6: working image — copy of target RGBA, RGB zeroed inside the
        # repair mask, everything else byte-identical.
        working = target_rgba.copy()
        working[repair_mask, 0:3] = 0
        Image.fromarray(working, mode="RGBA").save(
            working_image_path, format="PNG", optimize=True
        )

        target_mask_pixels = int(target_mask.sum())
        base.update(
            {
                "status": "success",
                "reason_code": REASON_OK,
                "message": None,
                "repair_mask_path": repair_mask_path.as_posix(),
                "repair_working_image_path": working_image_path.as_posix(),
                "repair_pixel_count": int(repair_mask.sum()),
                "repair_ratio_of_target_mask": (
                    float(repair_mask.sum()) / target_mask_pixels if target_mask_pixels else 0.0
                ),
                "projected_pixels_before_frame_clip": int(projected_before_clip),
                "projected_pixels_after_frame_clip": int(repair_mask.sum()),
            }
        )
        return base

    except (KeyError, ValueError) as exc:
        reason = getattr(exc, "reason_code", None)
        if reason is None:
            reason = "invalid_bbox" if isinstance(exc, ValueError) else "relation_read_error"
        return finish("failed", reason, str(exc))


def prepare_all(
    reviewed: dict[str, Any],
    relations: dict[str, Any],
    extraction_result_path: Path,
    repair_dir: Path,
) -> dict[str, Any]:
    assets = reviewed.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("reviewed JSON has no assets array")
    reviewed_assets = {asset["id"]: asset for asset in assets if isinstance(asset, dict) and "id" in asset}

    extraction_records = load_extraction_records(extraction_result_path)

    targets: list[dict[str, Any]] = []
    for relation in relations.get("relations", []):
        entry = prepare_target(
            relation,
            extraction_records,
            extraction_result_path,
            reviewed_assets,
            repair_dir,
        )
        record: dict[str, Any] = {
            "target_asset_id": entry["target_asset_id"],
            "status": entry["status"],
            "reason_code": entry["reason_code"],
            "message": entry.get("message"),
            "repair_input_path": None,
        }
        if entry["status"] != "failed" or entry.get("target_asset_path") is not None:
            input_path = repair_dir / entry["target_asset_id"] / "repair-input.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(
                json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            record["repair_input_path"] = input_path.as_posix()
        elif entry["status"] == "skipped":
            input_path = repair_dir / entry["target_asset_id"] / "repair-input.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(
                json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            record["repair_input_path"] = input_path.as_posix()
        targets.append(record)

    statuses = [t["status"] for t in targets]
    overall = "success" if statuses and all(s == "success" for s in statuses) else (
        "partial" if any(s == "success" for s in statuses) else "failed"
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": overall,
        "relations_schema": relations.get("schema_version", ""),
        "relations_file": "",
        "repair_dir": repair_dir.as_posix(),
        "targets": targets,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C1.5: prepare repair inputs")
    parser.add_argument("--reviewed", required=True, help="Path to reviewed-direct-assets.json")
    parser.add_argument("--relations", required=True, help="Path to repair-relations.json")
    parser.add_argument("--extraction-result", required=True, help="Path to Stage2-B extraction-result.json")
    parser.add_argument("--repair-dir", required=True, help="Output directory for repair inputs")
    parser.add_argument("--result", required=True, help="Path to write repair-preparation-result.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reviewed = load_json(Path(args.reviewed))
        relations = load_json(Path(args.relations))
        document = prepare_all(
            reviewed,
            relations,
            Path(args.extraction_result),
            Path(args.repair_dir),
        )
        document["relations_file"] = Path(args.relations).as_posix()
        errors = validation_errors(document, RESULT_SCHEMA_PATH)
        if errors:
            raise RuntimeError("Generated preparation result is invalid:\n" + "\n".join(errors))
        result_path = Path(args.result)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = {
        "status": document["status"],
        "targets": {
            t["target_asset_id"]: {"status": t["status"], "reason_code": t["reason_code"]}
            for t in document["targets"]
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if document["status"] in ("success", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
