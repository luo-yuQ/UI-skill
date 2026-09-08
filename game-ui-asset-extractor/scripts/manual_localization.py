"""Human secondary localization v0.1 (``human-secondary-localization-v0.1``).

Stage2-B companion entry for assets whose reviewed bbox alone does not
isolate the asset: a human supplies a coarse target bbox plus optional
prompt points, and optionally lists full occluders with their own bboxes.
The pipeline materializes the current-truth asset:

1. Target prompt: bbox + optional positive/negative points over the full
   source image (whole-image ``set_image``, encoded exactly once).
2. Each occluder is segmented independently from its own bbox (+ optional
   positive points). Negative points are NOT used for occluders: experiment
   002 showed they degrade SAM1 into a local eraser instead of excluding a
   complete occluder object.
3. ``visible_target_mask = target_mask AND NOT union(all occluder_masks)``
   (deterministic set algebra; 0..N occluders).
4. Binary-alpha RGBA: ``alpha = visible_mask * 255``, source RGB untouched.
   No morphology, feather, matting, repair, or occlusion completion.

Reuse contract: every SAM capability goes through the frozen
``scripts/sam_backend.py`` (loader, encode, box-only predict, winner rule,
postprocess, candidate metadata) without modifying it. The only new SAM
call path in this module is the points prompt — the "reserved for future
fallback" extension the frozen v0.1 baseline documented; it mirrors the
frozen ``predict_box`` call shape and adds ``point_coords``/``point_labels``.

Out of scope (Stage2-C or later versions): occlusion completion, repair,
Image-2, OpenCV inpainting, matting, feather, glow/shadow restoration.

Request/response documents follow ``schemas/manual-localization-request.schema.json``.
Validation evidence: ``runs/20260908_sam_point_prompt_poc_002`` (point
prompting), ``runs/20260908_sam_point_prompt_poc_003`` (occluder
subtraction), ``runs/20260908_visible_target_rgba_001`` (RGBA export).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sam_backend  # noqa: E402

LOCALIZATION_VERSION = "human-secondary-localization-v0.1"
SCHEMA_PATH = SCRIPTS_DIR.parent / "schemas" / "manual-localization-request.schema.json"
MODEL_TYPE = "vit_b"
ALPHA_RULE = "binary_mask_0_255"

TARGET_MASK_OUTPUT = "target-mask.png"
VISIBLE_MASK_OUTPUT = "visible-target-mask.png"
RGBA_OUTPUT = "visible-target-rgba.png"
CHECKERBOARD_OUTPUT = "visible-target-on-checkerboard.png"

CHECKERBOARD_SQUARE = 8
CHECKERBOARD_DARK = 204
CHECKERBOARD_LIGHT = 255


class ManualLocalizationError(RuntimeError):
    """An explicit, diagnosable manual-localization failure."""


def xyxy_of(bbox: dict[str, int]) -> tuple[int, int, int, int]:
    return (bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"])


def load_request(path: Path) -> dict[str, Any]:
    import extract_assets

    document = extract_assets.load_json(path)
    errors = extract_assets.validation_errors(document, SCHEMA_PATH)
    if errors:
        raise ManualLocalizationError(
            f"request does not satisfy {SCHEMA_PATH.name}: " + "; ".join(errors)
        )
    return document


def validate_semantics(
    document: dict[str, Any], width: int, height: int
) -> None:
    """Checks the schema cannot express: bbox/points in bounds, unique ids."""

    def check_bbox(where: str, bbox: dict[str, int]) -> None:
        x1, y1, x2, y2 = xyxy_of(bbox)
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ManualLocalizationError(
                f"{where} bbox {bbox} is outside the {width}x{height} source image"
            )

    def check_points(where: str, points: list[list[int]]) -> None:
        for x, y in points:
            if not (0 <= x < width and 0 <= y < height):
                raise ManualLocalizationError(
                    f"{where} point ({x}, {y}) is outside the {width}x{height} source image"
                )

    target = document["target"]
    check_bbox("target", target["bbox_source"])
    check_points("target positive", target.get("positive_points_source", []))
    check_points("target negative", target.get("negative_points_source", []))

    ids = [occluder["occluder_id"] for occluder in document.get("occluders", [])]
    if len(ids) != len(set(ids)):
        raise ManualLocalizationError(f"occluder_id values must be unique, got {ids}")
    for occluder in document.get("occluders", []):
        check_bbox(f"occluder '{occluder['occluder_id']}' bbox", occluder["bbox_source"])
        check_points(
            f"occluder '{occluder['occluder_id']}' positive",
            occluder.get("positive_points_source", []),
        )


def predict_with_points(
    predictor: Any,
    box_xyxy: tuple[int, int, int, int],
    positive_points: list[tuple[int, int]],
    negative_points: list[tuple[int, int]],
) -> tuple[np.ndarray, np.ndarray]:
    """Box + points prompt; mirrors the frozen ``predict_box`` call shape.

    Point coordinates are (N, 2) source pixels, labels use 1 = positive and
    0 = negative. Box-only prompts must go through ``sam_backend.predict_box``
    so the frozen box-only contract stays in exactly one place.
    """

    point_coords = list(positive_points) + list(negative_points)
    point_labels = [1] * len(positive_points) + [0] * len(negative_points)
    masks, scores, _ = predictor.predict(
        point_coords=np.asarray(point_coords, dtype=np.float32),
        point_labels=np.asarray(point_labels, dtype=np.int64),
        box=np.asarray(box_xyxy, dtype=np.float32),
        multimask_output=True,
    )
    return np.asarray(masks).astype(bool), np.asarray(scores, dtype=np.float64)


def segment_prompt(
    predictor: Any,
    bbox_xyxy: tuple[int, int, int, int],
    positive_points: list[tuple[int, int]],
    negative_points: list[tuple[int, int]],
) -> tuple[np.ndarray, dict[str, Any]]:
    """One prompt -> frozen winner rule -> frozen postprocess.

    Returns ``(final_mask, record)`` where ``record`` carries the full
    prompt/candidate/postprocess diagnostics for ``result.json``.
    """

    start = time.perf_counter()
    if not positive_points and not negative_points:
        masks, scores = sam_backend.predict_box(predictor, bbox_xyxy)
    else:
        masks, scores = predict_with_points(
            predictor, bbox_xyxy, positive_points, negative_points
        )
    predict_seconds = time.perf_counter() - start

    winner = sam_backend.select_winner(scores)
    winner_mask = masks[winner]
    start = time.perf_counter()
    final_mask, postprocess_stats = sam_backend.postprocess_sam_mask(
        winner_mask, positive_points=positive_points
    )
    postprocess_seconds = time.perf_counter() - start
    components = postprocess_stats["connected_components"]

    record = {
        "bbox_source_xyxy": list(bbox_xyxy),
        "positive_points_source": [list(p) for p in positive_points],
        "negative_points_source": [list(p) for p in negative_points],
        "sam_scores": [float(score) for score in scores],
        "winner_index": winner,
        "winner_sam_score": float(scores[winner]),
        "winner_raw_mask_area": int(winner_mask.sum()),
        "postprocessed_mask_area": int(final_mask.sum()),
        "postprocess": {
            "component_count": components["component_count"],
            "kept_component_count": components["kept_component_count"],
            "removed_component_count": components["removed_component_count"],
        },
        "candidates": sam_backend.build_candidates_metadata(masks, scores),
        "seconds": predict_seconds + postprocess_seconds,
    }
    return final_mask, record


def compute_visible_mask(
    target_mask: np.ndarray, occluder_masks: list[np.ndarray]
) -> np.ndarray:
    """``visible = target AND NOT union(occluders)`` for 0..N occluders."""

    visible = target_mask.astype(bool).copy()
    for occluder_mask in occluder_masks:
        if occluder_mask.shape != visible.shape:
            raise ManualLocalizationError(
                "occluder mask shape "
                f"{occluder_mask.shape} does not match target mask {visible.shape}"
            )
        visible &= ~occluder_mask.astype(bool)
    return visible


def build_rgba(source_rgb: np.ndarray, visible_mask: np.ndarray) -> np.ndarray:
    """Binary-alpha RGBA: source RGB untouched, alpha = mask * 255."""

    if visible_mask.shape != source_rgb.shape[:2]:
        raise ManualLocalizationError(
            f"mask shape {visible_mask.shape} does not match source {source_rgb.shape[:2]}"
        )
    alpha = visible_mask.astype(bool).astype(np.uint8) * 255
    return np.dstack([source_rgb, alpha])


def checkerboard_composite(rgba: np.ndarray, square: int = CHECKERBOARD_SQUARE) -> np.ndarray:
    """Composite an RGBA image over a gray checkerboard transparency preview."""

    height, width = rgba.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    checker = np.where(
        ((yy // square + xx // square) % 2) == 0, CHECKERBOARD_LIGHT, CHECKERBOARD_DARK
    ).astype(np.float32)
    board = np.dstack([checker, checker, checker])
    alpha = rgba[..., 3].astype(np.float32)[..., None] / 255.0
    composite = board * (1.0 - alpha) + rgba[..., :3].astype(np.float32) * alpha
    return composite.astype(np.uint8)


def save_mask(path: Path, mask: np.ndarray) -> None:
    Image.fromarray(mask.astype(bool).astype(np.uint8) * 255, mode="L").save(path)


def save_binary_png(path: Path, array: np.ndarray) -> None:
    Image.fromarray(array).save(path)


def execute_request(document: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Run the v0.1 pipeline; returns the ``result.json`` document."""

    with Image.open(document["source_image"]) as image:
        width, height = image.size
        source_rgb = np.asarray(image.convert("RGB"))

    validate_semantics(document, width, height)

    config = document.get("config", {})
    checkpoint = config.get("sam_checkpoint")
    if not checkpoint:
        raise ManualLocalizationError(
            "config.sam_checkpoint is required; checkpoints are never auto-resolved"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    predictor, predictor_info = sam_backend.load_sam_predictor(
        MODEL_TYPE, checkpoint, config.get("device", "auto")
    )

    start = time.perf_counter()
    sam_backend.encode_source(predictor, source_rgb)
    encode_seconds = time.perf_counter() - start

    target = document["target"]
    target_mask, target_record = segment_prompt(
        predictor,
        xyxy_of(target["bbox_source"]),
        [tuple(p) for p in target.get("positive_points_source", [])],
        [tuple(p) for p in target.get("negative_points_source", [])],
    )
    save_mask(output_dir / TARGET_MASK_OUTPUT, target_mask)

    occluder_records = []
    occluder_masks = []
    for occluder in document.get("occluders", []):
        occluder_mask, occluder_record = segment_prompt(
            predictor,
            xyxy_of(occluder["bbox_source"]),
            [tuple(p) for p in occluder.get("positive_points_source", [])],
            [],
        )
        occluder_mask_filename = f"occluder-{occluder['occluder_id']}-mask.png"
        save_mask(output_dir / occluder_mask_filename, occluder_mask)
        occluder_record["mask_output"] = occluder_mask_filename
        occluder_records.append({"occluder_id": occluder["occluder_id"], **occluder_record})
        occluder_masks.append(occluder_mask)

    visible_mask = compute_visible_mask(target_mask, occluder_masks)
    save_mask(output_dir / VISIBLE_MASK_OUTPUT, visible_mask)

    rgba = build_rgba(source_rgb, visible_mask)
    save_binary_png(output_dir / RGBA_OUTPUT, rgba)
    save_binary_png(
        output_dir / CHECKERBOARD_OUTPUT,
        checkerboard_composite(rgba, CHECKERBOARD_SQUARE),
    )

    removed = int((target_mask & ~visible_mask).sum())
    result = {
        "localization_version": LOCALIZATION_VERSION,
        "status": "success",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_image": str(Path(document["source_image"]).resolve()),
        "source_size": {"width": width, "height": height},
        "target": target_record,
        "occluders": occluder_records,
        "areas": {
            "target_mask": int(target_mask.sum()),
            "occluder_union_removed": removed,
            "visible_target_mask": int(visible_mask.sum()),
        },
        "alpha_rule": ALPHA_RULE,
        "outputs": {
            "target_mask": TARGET_MASK_OUTPUT,
            "occluder_masks": [r["mask_output"] for r in occluder_records],
            "visible_target_mask": VISIBLE_MASK_OUTPUT,
            "visible_target_rgba": RGBA_OUTPUT,
            "visible_target_on_checkerboard": CHECKERBOARD_OUTPUT,
            "result": "result.json",
        },
        "timing": {
            "image_encode_seconds": encode_seconds,
            "target_seconds": target_record["seconds"],
            "occluder_seconds_total": sum(r["seconds"] for r in occluder_records),
        },
        "predictor_info": predictor_info,
        "scope": {
            "occlusion_completion": "out_of_scope_stage2c",
            "repair": "out_of_scope",
            "matting": "out_of_scope",
            "feather": "out_of_scope",
        },
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Stage2-B manual secondary localization ({LOCALIZATION_VERSION})"
    )
    parser.add_argument("--request", required=True, type=Path,
                        help="path to a manual-localization-request JSON document")
    parser.add_argument("--output", type=Path, default=None,
                        help="run output directory (defaults to config.output_dir)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        document = load_request(args.request)
        config = document.get("config", {})
        if args.output is not None:
            output_dir = args.output
        elif config.get("output_dir"):
            output_dir = Path(config["output_dir"])
        else:
            raise ManualLocalizationError(
                "no output directory: pass --output or set config.output_dir"
            )
        result = execute_request(document, output_dir)
    except (ManualLocalizationError, FileNotFoundError) as exc:
        print(json.dumps({"localization_version": LOCALIZATION_VERSION,
                          "status": "error", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "localization_version": result["localization_version"],
        "status": result["status"],
        "output_dir": str(Path(args.output).resolve()) if args.output else config.get("output_dir"),
        "visible_target_mask_area": result["areas"]["visible_target_mask"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
