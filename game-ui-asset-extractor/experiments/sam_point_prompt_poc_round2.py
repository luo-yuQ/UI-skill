"""Round 2: fixed-target SAM1 point-prompt comparison (yellow highlighted sector).

Human-confirmed via ``target-debug.png``; inputs are fixed and must not be
re-selected here:

- source:   runs/20260902_direct-asset-discovery-007-production-client/source.png
- bbox:     (580, 465) 295x280 source XYWH
- P1:       (760, 525)  upper clean yellow band
- P2:       (660, 650)  lower sector surface, below pointer / left of card
- N1:       (740, 566)  inside the blue reward card
- N2:       (630, 615)  inside the brown central pointer

Case ladder (case 4 uses only N1 + N2 per the confirmed spec):

  1. box only               -> 01-mask-box-only.png
  2. box + P1               -> 02-mask-p1.png
  3. box + P1 + P2          -> 03-mask-p1-p2.png
  4. box + P1 + P2 + N1+N2  -> 04-mask-corrected.png

Reuses ``sam_point_prompt_poc`` (predict/overlay helpers) and the frozen
``sam_backend`` (load / encode / winner / postprocess) read-only; the
whole-image ``set_image()`` embedding is computed once and shared by all
cases. No RGBA, matting, repair, ROI encode, or automatic prompt tuning.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

EXPERIMENTS_DIR = Path(__file__).resolve().parent
if str(EXPERIMENTS_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS_DIR))

import sam_point_prompt_poc as poc  # noqa: E402  (adds scripts/ to sys.path)
import sam_backend  # noqa: E402

SOURCE = poc.REPO_ROOT / "runs/20260902_direct-asset-discovery-007-production-client/source.png"
OUT = poc.REPO_ROOT / "runs/20260908_sam_point_prompt_poc_002"

BBOX_XYWH = {"x": 580, "y": 465, "width": 295, "height": 280}
P1 = (760, 525)
P2 = (660, 650)
N1 = (740, 566)
N2 = (630, 615)

CROP = (560, 410, 900, 780)
ZOOM_SCALE = 3
OVERLAY_ALPHA = 0.45

CASES = [
    ("box-only", "box only", [], [], "01-mask-box-only.png"),
    ("box-p1", "box + P1", [P1], [], "02-mask-p1.png"),
    ("box-p1-p2", "box + P1 + P2", [P1, P2], [], "03-mask-p1-p2.png"),
    ("box-p1-p2-n1-n2", "box + P1 + P2 + N1 + N2", [P1, P2], [N1, N2], "04-mask-corrected.png"),
]


def main() -> None:
    bbox_xyxy = (
        BBOX_XYWH["x"],
        BBOX_XYWH["y"],
        BBOX_XYWH["x"] + BBOX_XYWH["width"],
        BBOX_XYWH["y"] + BBOX_XYWH["height"],
    )
    with Image.open(SOURCE) as image:
        width, height = image.size
        source_rgb = np.asarray(image.convert("RGB"))
    OUT.mkdir(parents=True, exist_ok=True)

    predictor, predictor_info = sam_backend.load_sam_predictor(
        "vit_b", str(poc.DEFAULT_CHECKPOINT), "auto"
    )

    start = time.perf_counter()
    sam_backend.encode_source(predictor, source_rgb)
    encode_seconds = time.perf_counter() - start

    start = time.perf_counter()
    predictor.predict(box=np.asarray(bbox_xyxy, dtype=np.float32), multimask_output=True)
    warmup_seconds = time.perf_counter() - start

    case_records = []
    for name, description, positives, negatives, output in CASES:
        masks, scores, predict_seconds = poc.predict_case(
            predictor, bbox_xyxy, positives, negatives
        )
        winner = sam_backend.select_winner(scores)
        winner_mask = masks[winner]

        start = time.perf_counter()
        final_mask, postprocess_stats = sam_backend.postprocess_sam_mask(
            winner_mask, positive_points=positives
        )
        postprocess_seconds = time.perf_counter() - start

        poc.save_mask_overlay(
            source_rgb, final_mask, bbox_xyxy, positives, negatives,
            CROP, ZOOM_SCALE, OVERLAY_ALPHA, OUT / output,
        )
        component_stats = postprocess_stats["connected_components"]
        case_records.append(
            {
                "name": name,
                "description": description,
                "points_used_source": {
                    "positive": [list(p) for p in positives],
                    "negative": [list(p) for p in negatives],
                },
                "point_labels_sent": [1] * len(positives) + [0] * len(negatives),
                "sam_scores": [float(score) for score in scores],
                "winner_index": winner,
                "winner_sam_score": float(scores[winner]),
                "winner_raw_mask_area": int(winner_mask.sum()),
                "postprocessed_mask_area": int(final_mask.sum()),
                "postprocess": {
                    "component_count": component_stats["component_count"],
                    "kept_component_count": component_stats["kept_component_count"],
                    "removed_component_count": component_stats["removed_component_count"],
                },
                "predict_seconds": predict_seconds,
                "postprocess_seconds": postprocess_seconds,
                "candidates": sam_backend.build_candidates_metadata(masks, scores),
                "mask_output": output,
            }
        )
        print(
            f"[{name}] winner={winner} score={scores[winner]:.6f} "
            f"raw_area={int(winner_mask.sum())} final_area={int(final_mask.sum())} "
            f"predict={predict_seconds:.3f}s"
        )

    result = {
        "experiment": "sam1-point-prompt-poc-round2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "target": "yellow highlighted wheel sector behind the blue wood reward card",
        "target_confirmed_by": "target-debug.png (human-confirmed)",
        "scope": {
            "frozen_files_read_only": [
                "game-ui-asset-extractor/scripts/sam_backend.py",
                "game-ui-asset-extractor/scripts/extract_assets.py",
            ],
            "reused": [
                "sam_backend.load_sam_predictor",
                "sam_backend.encode_source",
                "sam_backend.predict_box",
                "sam_backend.select_winner",
                "sam_backend.postprocess_sam_mask",
                "sam_backend.build_candidates_metadata",
                "sam_point_prompt_poc.predict_case",
                "sam_point_prompt_poc.save_mask_overlay",
            ],
            "roi_strategy": "whole-image set_image, encoded once, shared by all 4 cases",
        },
        "source_image": str(SOURCE.resolve()),
        "image_size": {"width": width, "height": height},
        "checkpoint": str(poc.DEFAULT_CHECKPOINT.resolve()),
        "predictor_info": predictor_info,
        "env": {
            "torch": sys.modules["torch"].__version__,
            "cuda_available": bool(sys.modules["torch"].cuda.is_available()),
        },
        "sam_call_contract": {
            "box": "XYXY = (x, y, x + w, y + h) source pixels",
            "point_coords": "Nx2 source pixels",
            "point_labels": {"1": "positive", "0": "negative"},
            "multimask_output": True,
        },
        "bbox_source": BBOX_XYWH,
        "bbox_source_xyxy": list(bbox_xyxy),
        "positive_points_source": [list(P1), list(P2)],
        "negative_points_source": [list(N1), list(N2)],
        "case_ladder": {record["name"]: record["description"] for record in case_records},
        "timing": {
            "image_encode_seconds": encode_seconds,
            "warmup_predict_seconds": warmup_seconds,
        },
        "cases": case_records,
    }
    (OUT / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"done: {OUT}")


if __name__ == "__main__":
    main()
