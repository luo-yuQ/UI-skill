#!/usr/bin/env python3
"""Experiment adapter — batch SAM run → formal Stage2-B extraction contract.

Boundary (explicit):

- PRODUCTION contract: ``game-ui-asset-repairer/prepare_repair_inputs.py``
  only understands the formal extraction-result.json schema emitted by
  ``game-ui-asset-extractor/scripts/extract_assets.py`` — records with
  ``asset_id``, ``final_bbox``, ``extraction_roi``, ``output_path``,
  ``mask_path``, ``status``.
- EXPERIMENT data: the frozen ``20260904_sam_box_only_batch_001`` run keeps
  per-asset ``result.json`` files (bbox-local winner masks, no
  extraction_roi, no formal result document). This adapter re-expresses
  that data in the formal contract WITHOUT modifying the frozen batch run
  or the production code.

Mapping:

- ``final_bbox``       <- result.json ``bbox_xywh`` (reviewed bbox_source)
- ``extraction_roi``   <- identical to final_bbox (batch masks/RGBA are
  tight bbox-local crops, so frame == final_bbox, kind resolves to
  ``final_bbox`` semantics with zero offset)
- ``output_path``      <- the batch ``filtered-rgba.png``
- ``mask_path``        <- the batch ``filtered-mask.png`` (final
  postprocessed mask — close + connected-component filtering)
- ``status``           <- ``success`` when both PNGs exist
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def convert_batch_run(batch_dir: Path) -> dict:
    batch_result_path = batch_dir / "batch-result.json"
    if not batch_result_path.is_file():
        raise FileNotFoundError(batch_result_path)
    batch_result = json.loads(batch_result_path.read_text(encoding="utf-8-sig"))

    assets: list[dict] = []
    for entry in batch_result.get("results", []):
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str):
            continue
        if entry.get("status") != "success":
            assets.append(
                {
                    "asset_id": asset_id,
                    "asset_type": "unknown",
                    "extraction_mode": "foreground_extract",
                    "status": "failed",
                    "source_image": batch_result.get("source_image", ""),
                    "final_bbox": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "extraction_roi": None,
                    "roi_padding": 0,
                    "final_bbox_offset": {"x": 0, "y": 0},
                    "background_method": None,
                    "background_rgb": None,
                    "background_parameters": {},
                    "mask_method": None,
                    "mask_threshold": None,
                    "mask_parameters": {},
                    "alpha_parameters": {},
                    "output_path": None,
                    "mask_path": None,
                    "failure_reason": entry.get("reason", "batch entry not successful"),
                }
            )
            continue

        asset_dir = batch_dir / asset_id
        result_json = asset_dir / "result.json"
        filtered_mask = asset_dir / "filtered-mask.png"
        filtered_rgba = asset_dir / "filtered-rgba.png"
        if not result_json.is_file() or not filtered_mask.is_file() or not filtered_rgba.is_file():
            assets.append(
                {
                    "asset_id": asset_id,
                    "asset_type": "unknown",
                    "extraction_mode": "foreground_extract",
                    "status": "failed",
                    "source_image": batch_result.get("source_image", ""),
                    "final_bbox": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "extraction_roi": None,
                    "roi_padding": 0,
                    "final_bbox_offset": {"x": 0, "y": 0},
                    "background_method": None,
                    "background_rgb": None,
                    "background_parameters": {},
                    "mask_method": None,
                    "mask_threshold": None,
                    "mask_parameters": {},
                    "alpha_parameters": {},
                    "output_path": None,
                    "mask_path": None,
                    "failure_reason": "batch asset outputs incomplete",
                }
            )
            continue

        result = json.loads(result_json.read_text(encoding="utf-8-sig"))
        bbox = result["bbox_xywh"]
        assets.append(
            {
                "asset_id": asset_id,
                "asset_type": "unknown",
                "extraction_mode": "foreground_extract",
                "status": "success",
                "source_image": batch_result.get("source_image", ""),
                "final_bbox": {
                    "x": int(bbox["x"]),
                    "y": int(bbox["y"]),
                    "width": int(bbox["width"]),
                    "height": int(bbox["height"]),
                },
                # batch masks/RGBA are tight bbox-local crops: the frame that
                # makes their local coordinates resolve to source pixels is
                # exactly final_bbox.
                "extraction_roi": {
                    "x": int(bbox["x"]),
                    "y": int(bbox["y"]),
                    "width": int(bbox["width"]),
                    "height": int(bbox["height"]),
                },
                "roi_padding": 0,
                "final_bbox_offset": {"x": 0, "y": 0},
                "background_method": None,
                "background_rgb": None,
                "background_parameters": {},
                "mask_method": "sam1_box_v0",
                "mask_threshold": None,
                "mask_parameters": {
                    "origin": "experiment_adapter:20260904_sam_box_only_batch_001",
                    "winner_index": result.get("winner_index"),
                    "winner_score": result.get("winner_score"),
                },
                "alpha_parameters": {
                    "dilation_radius": 0,
                    "gaussian_blur_radius": 0.0,
                    "source_alpha_rule": "replace",
                    "alpha_representation": "straight",
                },
                "output_path": filtered_rgba.resolve().as_posix(),
                "mask_path": filtered_mask.resolve().as_posix(),
            }
        )

    return {
        "schema_version": "0.1",
        "status": "success",
        "source_image": batch_result.get("source_image", ""),
        "source_size": None,
        "backend": "sam1_vit_b",
        "config": {},
        "assets": assets,
        "experiment_adapter": {
            "name": "batch_sam_box_only_adapter_v0.1",
            "batch_run": batch_dir.name,
            "note": (
                "experiment-only conversion of the frozen batch run into the "
                "formal extraction contract; production extraction must use "
                "game-ui-asset-extractor/scripts/extract_assets.py"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert batch SAM run to formal extraction contract")
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        document = convert_batch_run(Path(args.batch_dir))
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ok = sum(1 for a in document["assets"] if a["status"] == "success")
    print(f"converted {ok}/{len(document['assets'])} assets -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
