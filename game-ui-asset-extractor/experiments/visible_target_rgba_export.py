"""Experiment 004: deterministic visible_target_mask -> RGBA conversion.

Converts the validated visible target mask (experiment 003) into a real RGBA
asset: source RGB preserved inside the mask, alpha = 255 where mask = 1 and
0 where mask = 0. No morphology, blur, feather, matting, or repair.

Mask provenance: experiment 003 validated ``visible = target AND NOT
occluder`` but persisted only overlay PNGs, not raw mask bits. This script
re-executes the exact 003 pipeline (the same 003 module functions, same
frozen prompts, same frozen postprocess) purely to materialize the mask
bits, then hard-verifies every SAM-derived statistic against 003's
``result.json`` BEFORE writing any output; any mismatch aborts. No prompt
changes, no new segmentation variants, no SAM2.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

EXPERIMENTS_DIR = Path(__file__).resolve().parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

import sam_point_prompt_poc as poc  # noqa: E402  (adds scripts/ to sys.path)
import sam_point_prompt_poc_round3_occluder_subtraction as exp003  # noqa: E402
import sam_backend  # noqa: E402

SOURCE = exp003.SOURCE
REF_RESULT = poc.REPO_ROOT / "runs/20260908_sam_point_prompt_poc_003/result.json"
OUT = poc.REPO_ROOT / "runs/20260908_visible_target_rgba_001"

MASK_OUTPUT = "01-visible-mask.png"
OVERLAY_OUTPUT = "02-visible-mask-overlay.png"
RGBA_OUTPUT = "03-visible-target-rgba.png"
CHECKER_OUTPUT = "04-visible-target-on-checkerboard.png"
ALPHA_RULE = "binary_mask_0_255"
CHECKER_SQUARE = 8
CHECKER_DARK = 204
CHECKER_LIGHT = 255


def reproduce_visible_mask(source_rgb: np.ndarray) -> tuple[np.ndarray, dict]:
    """Re-run the exact 003 mask pipeline and verify it against 003's records."""

    ref = json.loads(REF_RESULT.read_text(encoding="utf-8"))
    predictor, predictor_info = sam_backend.load_sam_predictor(
        "vit_b", str(poc.DEFAULT_CHECKPOINT), "auto"
    )
    sam_backend.encode_source(predictor, source_rgb)

    target_xyxy = exp003.xyxy_of(exp003.TARGET_BBOX_XYWH)
    occluder_xyxy = exp003.xyxy_of(exp003.OCCLUDER_BBOX_XYWH)
    _, target_mask, target_score, _ = exp003.segment(
        predictor, target_xyxy, [exp003.TARGET_P1, exp003.TARGET_P2]
    )
    _, occluder_mask, occluder_score, _ = exp003.segment(predictor, occluder_xyxy, [])
    visible_mask = target_mask & ~occluder_mask

    checks = {
        "target_mask_area": (
            int(target_mask.sum()), ref["areas"]["target_mask"],
        ),
        "occluder_mask_area": (
            int(occluder_mask.sum()), ref["areas"]["occluder_mask"],
        ),
        "removed_pixels": (
            int((target_mask & occluder_mask).sum()), ref["areas"]["removed_pixels"],
        ),
        "visible_mask_area": (
            int(visible_mask.sum()), ref["areas"]["visible_target_mask"],
        ),
        "target_winner_score": (
            round(float(target_score), 6),
            round(float(ref["target_mask_source"]["winner_sam_score"]), 6),
        ),
        "occluder_winner_score": (
            round(float(occluder_score), 6),
            round(float(ref["occluder"]["winner_score"]), 6),
        ),
    }
    mismatched = {name: value for name, value in checks.items() if value[0] != value[1]}
    if mismatched:
        raise SystemExit(
            "ABORT: reproduced mask does not match experiment 003 records; "
            f"nothing written. Mismatches: {mismatched}"
        )
    verification = {
        name: {"recomputed": value[0], "recorded_003": value[1]}
        for name, value in checks.items()
    }
    return visible_mask, verification


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as image:
        width, height = image.size
        source_rgb = np.asarray(image.convert("RGB"))

    visible_mask, verification = reproduce_visible_mask(source_rgb)

    mask_u8 = visible_mask.astype(np.uint8) * 255
    Image.fromarray(mask_u8, mode="L").save(OUT / MASK_OUTPUT)

    poc.save_mask_overlay(
        source_rgb, visible_mask, exp003.xyxy_of(exp003.TARGET_BBOX_XYWH),
        [exp003.TARGET_P1, exp003.TARGET_P2], [],
        exp003.CROP, exp003.ZOOM_SCALE, exp003.OVERLAY_ALPHA, OUT / OVERLAY_OUTPUT,
    )

    rgba = np.dstack([source_rgb, mask_u8])
    Image.fromarray(rgba, mode="RGBA").save(OUT / RGBA_OUTPUT)

    yy, xx = np.mgrid[0:height, 0:width]
    checker = np.where(
        ((yy // CHECKER_SQUARE + xx // CHECKER_SQUARE) % 2) == 0,
        CHECKER_LIGHT, CHECKER_DARK,
    ).astype(np.uint8)
    board = np.dstack([checker, checker, checker]).astype(np.float32)
    alpha = mask_u8.astype(np.float32)[..., None] / 255.0
    composite = board * (1.0 - alpha) + source_rgb.astype(np.float32) * alpha
    Image.fromarray(composite.astype(np.uint8), mode="RGB").save(OUT / CHECKER_OUTPUT)

    result = {
        "experiment": "visible-target-rgba-004",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_image": str(SOURCE.resolve()),
        "visible_mask_source": {
            "experiment_run": "runs/20260908_sam_point_prompt_poc_003",
            "derivation": "target_mask (round2 case3: box + P1 + P2) AND NOT occluder_mask (card box-only)",
            "provenance_note": (
                "003 validated the mask but persisted only overlay PNGs; 004 re-executed "
                "the identical frozen 003 pipeline (same module functions, same prompts, "
                "same postprocess) to materialize the mask bits, then verified every "
                "SAM-derived statistic against 003 result.json before writing outputs."
            ),
            "verification": verification,
        },
        "source_size": {"width": width, "height": height},
        "mask_size": {"width": width, "height": height},
        "visible_pixel_count": int(visible_mask.sum()),
        "transparent_pixel_count": int((~visible_mask).sum()),
        "output_rgba": RGBA_OUTPUT,
        "alpha_rule": ALPHA_RULE,
        "outputs": {
            "visible_mask": MASK_OUTPUT,
            "visible_mask_overlay": OVERLAY_OUTPUT,
            "visible_target_rgba": RGBA_OUTPUT,
            "visible_target_on_checkerboard": CHECKER_OUTPUT,
        },
        "status": "ok",
    }
    (OUT / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"done: visible={int(visible_mask.sum())} "
        f"transparent={int((~visible_mask).sum())} -> {OUT}"
    )


if __name__ == "__main__":
    main()
