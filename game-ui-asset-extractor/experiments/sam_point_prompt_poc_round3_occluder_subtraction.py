"""Experiment 003: occluder segmentation + deterministic mask subtraction.

Validates whether ``visible_target = target_mask AND NOT occluder_mask``
removes the blue wood reward card from the round-2 yellow highlighted sector
mask cleanly.

Fixed inputs (no re-selection allowed here):

- source:      runs/20260902_direct-asset-discovery-007-production-client/source.png
- target:      round-2 case 3 (``bbox + P1 + P2``), reproduced deterministically:
               bbox (580, 465) 295x280, P1 (760, 525), P2 (660, 650)
- occluder:    blue wood reward card, bbox (680, 550) 137x135 (human-measured
               on the round-2 10px grid); reserve positive point (748, 566)
               on the clean blue strip, only used for case B.

Stages (``--stage``):

- ``debug``:   render ``01-occluder-debug.png`` (target bbox + occluder bbox +
               zoom panel) and stop. SAM is NOT invoked. Run this first and
               wait for human confirmation.
- ``run``:     after bbox confirmation: re-run target case 3, segment the
               occluder (case A box-only; case B box + positive point with
               ``--occluder-case b`` or ``ab``), subtract, write overlays
               ``02``-``06`` and ``result.json``.

Reuse is read-only: frozen ``sam_backend`` plus ``sam_point_prompt_poc``
helpers. No repair, no matting, no ROI encode, no SAM2, no production changes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

EXPERIMENTS_DIR = Path(__file__).resolve().parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

import sam_point_prompt_poc as poc  # noqa: E402  (adds scripts/ to sys.path)
import sam_backend  # noqa: E402

SOURCE = poc.REPO_ROOT / "runs/20260902_direct-asset-discovery-007-production-client/source.png"
OUT = poc.REPO_ROOT / "runs/20260908_sam_point_prompt_poc_003"
ROUND2_RESULT = poc.REPO_ROOT / "runs/20260908_sam_point_prompt_poc_002/result.json"

# Round-2 human-confirmed target prompt (fixed).
TARGET_BBOX_XYWH = {"x": 580, "y": 465, "width": 295, "height": 280}
TARGET_P1 = (760, 525)
TARGET_P2 = (660, 650)

# Round-2 measurement of the blue wood reward card (fixed).
OCCLUDER_BBOX_XYWH = {"x": 680, "y": 550, "width": 137, "height": 135}
OCCLUDER_P1 = (748, 566)

CROP = (560, 410, 900, 780)
ZOOM_SCALE = 3
OVERLAY_ALPHA = 0.45

TARGET_COLOR = (255, 200, 0)
OCCLUDER_COLOR = (0, 224, 255)

TARGET_DEBUG_OUTPUT = "01-occluder-debug.png"
TARGET_BEFORE_OUTPUT = "04-target-mask-before-subtraction.png"
OCCLUDER_MASK_OUTPUT = "05-occluder-mask.png"
VISIBLE_OUTPUT = "06-target-mask-after-subtraction.png"
OCCLUDER_CASE_OUTPUTS = {"a": "02-occluder-mask-box-only.png", "b": "03-occluder-mask-box-plus-p1.png"}


def xyxy_of(bbox_xywh: dict[str, int]) -> tuple[int, int, int, int]:
    return (
        bbox_xywh["x"],
        bbox_xywh["y"],
        bbox_xywh["x"] + bbox_xywh["width"],
        bbox_xywh["y"] + bbox_xywh["height"],
    )


def load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def draw_labeled_bbox(
    draw: ImageDraw.ImageDraw,
    bbox_xyxy: tuple[int, int, int, int],
    color: tuple[int, int, int],
    label: str,
    *,
    offset: tuple[int, int],
    scale: int,
) -> None:
    ox, oy = offset
    x1, y1, x2, y2 = bbox_xyxy
    draw.rectangle(
        [(x1 - ox) * scale, (y1 - oy) * scale, (x2 - ox) * scale, (y2 - oy) * scale],
        outline=color,
        width=max(2, 2 * scale),
    )
    font = load_font(max(14, 8 * scale))
    ty = (y1 - oy) * scale - 20 * scale
    if ty < 0:
        ty = (y2 - oy) * scale + 4 * scale
    draw.text(
        ((x1 - ox) * scale, ty),
        label,
        fill=color,
        font=font,
        stroke_width=max(1, scale),
        stroke_fill=(0, 0, 0),
    )


def render_debug(source_rgb: np.ndarray) -> None:
    target_xyxy = xyxy_of(TARGET_BBOX_XYWH)
    occluder_xyxy = xyxy_of(OCCLUDER_BBOX_XYWH)
    full = Image.fromarray(source_rgb)
    draw = ImageDraw.Draw(full)
    draw_labeled_bbox(draw, target_xyxy, TARGET_COLOR, "TARGET (round2 case3)", offset=(0, 0), scale=1)
    draw_labeled_bbox(draw, occluder_xyxy, OCCLUDER_COLOR, "OCCLUDER (blue card)", offset=(0, 0), scale=1)
    cx1, cy1, cx2, cy2 = CROP
    zoomed = Image.fromarray(source_rgb[cy1:cy2, cx1:cx2]).resize(
        ((cx2 - cx1) * ZOOM_SCALE, (cy2 - cy1) * ZOOM_SCALE), Image.NEAREST
    )
    draw = ImageDraw.Draw(zoomed)
    draw_labeled_bbox(
        draw, target_xyxy, TARGET_COLOR, "TARGET", offset=(cx1, cy1), scale=ZOOM_SCALE
    )
    draw_labeled_bbox(
        draw, occluder_xyxy, OCCLUDER_COLOR, "OCCLUDER", offset=(cx1, cy1), scale=ZOOM_SCALE
    )
    poc.compose_panels(full, zoomed).save(OUT / TARGET_DEBUG_OUTPUT)


def segment(
    predictor,
    bbox_xyxy: tuple[int, int, int, int],
    positives: list[tuple[int, int]],
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """One full prompt -> winner -> frozen postprocess; returns mask, final, score, seconds."""

    masks, scores, predict_seconds = poc.predict_case(predictor, bbox_xyxy, positives, [])
    winner = sam_backend.select_winner(scores)
    winner_mask = masks[winner]
    start = time.perf_counter()
    final_mask, _ = sam_backend.postprocess_sam_mask(winner_mask, positive_points=positives)
    postprocess_seconds = time.perf_counter() - start
    return winner_mask, final_mask, float(scores[winner]), predict_seconds + postprocess_seconds


def save_overlay(source_rgb: np.ndarray, mask: np.ndarray, path: Path) -> None:
    poc.save_mask_overlay(
        source_rgb, mask, xyxy_of(TARGET_BBOX_XYWH),
        [TARGET_P1, TARGET_P2], [],
        CROP, ZOOM_SCALE, OVERLAY_ALPHA, path,
    )


def run(source_rgb: np.ndarray, occluder_cases: list[str]) -> dict:
    target_xyxy = xyxy_of(TARGET_BBOX_XYWH)
    occluder_xyxy = xyxy_of(OCCLUDER_BBOX_XYWH)
    predictor, predictor_info = sam_backend.load_sam_predictor(
        "vit_b", str(poc.DEFAULT_CHECKPOINT), "auto"
    )
    start = time.perf_counter()
    sam_backend.encode_source(predictor, source_rgb)
    encode_seconds = time.perf_counter() - start
    start = time.perf_counter()
    predictor.predict(box=np.asarray(target_xyxy, dtype=np.float32), multimask_output=True)
    warmup_seconds = time.perf_counter() - start

    # Target mask: round-2 case 3 reproduced (frozen prompt, frozen postprocess).
    target_raw, target_mask, target_score, target_seconds = segment(
        predictor, target_xyxy, [TARGET_P1, TARGET_P2]
    )
    save_overlay(source_rgb, target_mask, OUT / TARGET_BEFORE_OUTPUT)

    # Occluder segmentation per confirmed case ladder.
    occluder_results: dict[str, dict] = {}
    for case in occluder_cases:
        positives = [OCCLUDER_P1] if case == "b" else []
        occluder_raw, occluder_final, score, seconds = segment(
            predictor, occluder_xyxy, positives
        )
        save_overlay(source_rgb, occluder_final, OUT / OCCLUDER_CASE_OUTPUTS[case])
        occluder_results[case] = {
            "description": "box only" if case == "a" else "box + 1 positive point",
            "positive_points_source": [list(p) for p in positives],
            "winner_score": score,
            "winner_raw_mask_area": int(occluder_raw.sum()),
            "postprocessed_mask_area": int(occluder_final.sum()),
            "seconds": seconds,
            "mask_output": OCCLUDER_CASE_OUTPUTS[case],
            "_mask": occluder_final,
        }
        print(
            f"[occluder-{case}] score={score:.6f} raw={int(occluder_raw.sum())} "
            f"final={int(occluder_final.sum())}"
        )
    chosen = occluder_cases[-1]
    occluder_mask = occluder_results[chosen]["_mask"]
    save_overlay(source_rgb, occluder_mask, OUT / OCCLUDER_MASK_OUTPUT)

    visible_mask = target_mask & ~occluder_mask
    save_overlay(source_rgb, visible_mask, OUT / VISIBLE_OUTPUT)

    round2 = json.loads(ROUND2_RESULT.read_text(encoding="utf-8"))
    round2_case3 = next(c for c in round2["cases"] if c["name"] == "box-p1-p2")
    result = {
        "experiment": "sam1-occluder-subtraction-003",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scheme": "visible_target_mask = target_mask AND NOT occluder_mask (deterministic)",
        "target_mask_source": {
            "experiment": "runs/20260908_sam_point_prompt_poc_002 (round 2)",
            "case": "box-p1-p2 (box + P1 + P2)",
            "reproduced_this_run": True,
            "round2_recorded_postprocessed_mask_area": round2_case3["postprocessed_mask_area"],
            "reproduced_postprocessed_mask_area": int(target_mask.sum()),
            "matches_round2": int(target_mask.sum()) == round2_case3["postprocessed_mask_area"],
            "winner_sam_score": target_score,
        },
        "occluder": {
            "description": "blue wood reward card (not the log icon)",
            "bbox_source": OCCLUDER_BBOX_XYWH,
            "bbox_source_xyxy": list(occluder_xyxy),
            "confirmed_via": "01-occluder-debug.png (human-confirmed)",
            "positive_point_used": {
                "case": chosen,
                "point": list(OCCLUDER_P1) if chosen == "b" else None,
            },
            "winner_score": occluder_results[chosen]["winner_score"],
            "cases": {
                key: {k: v for k, v in payload.items() if k != "_mask"}
                for key, payload in occluder_results.items()
            },
            "chosen_case": chosen,
            "mask_output": OCCLUDER_MASK_OUTPUT,
        },
        "timing": {
            "image_encode_seconds": encode_seconds,
            "warmup_predict_seconds": warmup_seconds,
            "target_case3_seconds": target_seconds,
        },
        "areas": {
            "target_mask": int(target_mask.sum()),
            "occluder_mask": int(occluder_mask.sum()),
            "visible_target_mask": int(visible_mask.sum()),
            "removed_pixels": int((target_mask & occluder_mask).sum()),
        },
        "outputs": {
            "occluder_debug": TARGET_DEBUG_OUTPUT,
            "target_before_subtraction": TARGET_BEFORE_OUTPUT,
            "occluder_mask": OCCLUDER_MASK_OUTPUT,
            "visible_target_mask": VISIBLE_OUTPUT,
        },
        "predictor_info": predictor_info,
    }
    (OUT / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"[visible] target={int(target_mask.sum())} occluder={int(occluder_mask.sum())} "
        f"removed={int((target_mask & occluder_mask).sum())} "
        f"visible={int(visible_mask.sum())}"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", choices=("debug", "run"), default="debug")
    parser.add_argument("--occluder-case", choices=("a", "b", "ab"), default="a",
                        help="occluder prompt ladder for --stage run")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as image:
        source_rgb = np.asarray(image.convert("RGB"))
    if args.stage == "debug":
        render_debug(source_rgb)
        print(f"debug written, SAM not invoked: {OUT / TARGET_DEBUG_OUTPUT}")
        return
    cases = ["a"] if args.occluder_case == "a" else (
        ["b"] if args.occluder_case == "b" else ["a", "b"]
    )
    run(source_rgb, cases)


if __name__ == "__main__":
    main()
