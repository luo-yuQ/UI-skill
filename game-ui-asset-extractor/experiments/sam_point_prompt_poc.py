"""Minimal SAM1 point-prompt PoC (experiment-only; frozen v0.1 untouched).

This script validates point prompting on top of the frozen box-only baseline
without modifying any frozen implementation:

- ``scripts/sam_backend.py`` and ``scripts/extract_assets.py`` are imported
  read-only; every frozen constant and call path is reused as-is.
- The whole-image ``set_image()`` strategy is kept: the full source image is
  encoded exactly once per run and shared by all cases. No ROI encoding.
- SAM2 is out of scope; this PoC only re-prompts SAM1 ViT-B.

Fixed case ladder (each case is an independent ``predict`` call):

  1. box only                                  (frozen ``predict_box`` path)
  2. box + P1
  3. box + P1 + P2
  4. box + P1 + P2 + negative points           ("corrected")

SAM1 prompt contract: box in XYXY = (x, y, x + w, y + h) source pixels
(same as ``sam_backend.bbox_edges``); ``point_coords`` is an (N, 2) array in
source pixels; ``point_labels`` uses 1 = positive, 0 = negative.

Winner selection and mask postprocess reuse the frozen rules: max SAM score,
then 3x3 close + 8-connected component filter. The frozen component filter
already accepts positive points (hit components are always kept); negative
points do not participate in postprocess.

Inputs: source image, bbox (source XYWH), positive points (source XY),
negative points (source XY), SAM checkpoint. Outputs land in one run
directory: ``01-source-debug.png``, one overlay per case
(``02``-``05``), and ``result.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sam_backend  # noqa: E402  (frozen module, imported read-only)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT = REPO_ROOT / "models" / "sam" / "sam_vit_b_01ec64.pth"

EXPERIMENT_ID = "sam1-point-prompt-poc"

DEBUG_OUTPUT = "01-source-debug.png"
CASE_OUTPUTS = ("02-mask-box-only.png", "03-mask-box-p1.png", "04-mask-box-p1-p2.png", "05-mask-corrected.png")

OVERLAY_COLOR = (255, 32, 32)
OVERLAY_OUTLINE_COLOR = (255, 255, 255)
BBOX_COLOR = (255, 200, 0)
POSITIVE_COLOR = (0, 220, 64)
NEGATIVE_COLOR = (255, 48, 48)
PANEL_BACKGROUND = (24, 24, 24)


def parse_point(token: str) -> tuple[int, int]:
    parts = [part.strip() for part in token.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"point must be 'x,y', got '{token}'")
    return int(parts[0]), int(parts[1])


def check_in_bounds(point: tuple[int, int], width: int, height: int, what: str) -> None:
    x, y = point
    if not (0 <= x < width and 0 <= y < height):
        raise SystemExit(f"{what} point {point} is outside the {width}x{height} source image")


def build_cases(
    positives: list[tuple[int, int]], negatives: list[tuple[int, int]]
) -> list[dict[str, object]]:
    """Fixed ladder: box / box+P1 / box+P1+P2 / box+P1+P2+negatives."""

    if len(positives) < 2:
        raise SystemExit("the fixed 4-case ladder needs at least 2 positive points (P1, P2)")
    p1, p2 = positives[0], positives[1]
    return [
        {
            "name": "box-only",
            "description": "box only",
            "positive_points": [],
            "negative_points": [],
            "output": CASE_OUTPUTS[0],
        },
        {
            "name": "box-p1",
            "description": "box + P1",
            "positive_points": [p1],
            "negative_points": [],
            "output": CASE_OUTPUTS[1],
        },
        {
            "name": "box-p1-p2",
            "description": "box + P1 + P2",
            "positive_points": [p1, p2],
            "negative_points": [],
            "output": CASE_OUTPUTS[2],
        },
        {
            "name": "box-p1-p2-neg",
            "description": "box + P1 + P2 + negative points",
            "positive_points": [p1, p2],
            "negative_points": list(negatives),
            "output": CASE_OUTPUTS[3],
        },
    ]


def load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def annotate(
    image: Image.Image,
    bbox_xyxy: tuple[int, int, int, int],
    positives: list[tuple[int, int]],
    negatives: list[tuple[int, int]],
    *,
    offset: tuple[int, int] = (0, 0),
    scale: int = 1,
) -> None:
    """Draw bbox + points in place; coordinates are source pixels transformed by (offset, scale)."""

    ox, oy = offset
    draw = ImageDraw.Draw(image)
    line_width = max(2, 2 * scale)
    font = load_font(max(14, 9 * scale))
    x1, y1, x2, y2 = bbox_xyxy
    draw.rectangle(
        [(x1 - ox) * scale, (y1 - oy) * scale, (x2 - ox) * scale, (y2 - oy) * scale],
        outline=BBOX_COLOR,
        width=line_width,
    )
    for label, points, color in (("P", positives, POSITIVE_COLOR), ("N", negatives, NEGATIVE_COLOR)):
        for index, (px, py) in enumerate(points):
            cx, cy = (px - ox) * scale, (py - oy) * scale
            radius = max(6, 5 * scale)
            if color is POSITIVE_COLOR:
                draw.ellipse(
                    [cx - radius, cy - radius, cx + radius, cy + radius],
                    fill=color,
                    outline=(255, 255, 255),
                    width=max(2, scale),
                )
            else:
                draw.ellipse(
                    [cx - radius, cy - radius, cx + radius, cy + radius],
                    outline=(255, 255, 255),
                    width=max(2, scale),
                )
                arm = max(4, 3 * scale)
                draw.line(
                    [(cx - arm, cy - arm), (cx + arm, cy + arm)],
                    fill=color,
                    width=max(3, 2 * scale),
                )
                draw.line(
                    [(cx - arm, cy + arm), (cx + arm, cy - arm)],
                    fill=color,
                    width=max(3, 2 * scale),
                )
            text = f"{label}{index + 1}"
            draw.text(
                (cx + radius + 2 * scale, cy - radius - 2 * scale),
                text,
                fill=(255, 255, 255),
                font=font,
                stroke_width=max(1, scale),
                stroke_fill=(0, 0, 0),
            )


def zoom_crop(
    bbox_xyxy: tuple[int, int, int, int], width: int, height: int, pad: int
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox_xyxy
    return (max(0, x1 - pad), max(0, y1 - pad), min(width, x2 + pad), min(height, y2 + pad))


def compose_panels(full: Image.Image, zoomed: Image.Image) -> Image.Image:
    gutter = 12
    canvas = Image.new(
        "RGB",
        (full.width + gutter + zoomed.width, max(full.height, zoomed.height)),
        PANEL_BACKGROUND,
    )
    canvas.paste(full, (0, 0))
    canvas.paste(zoomed, (full.width + gutter, 0))
    return canvas


def save_debug_image(
    source_rgb: np.ndarray,
    bbox_xyxy: tuple[int, int, int, int],
    positives: list[tuple[int, int]],
    negatives: list[tuple[int, int]],
    crop: tuple[int, int, int, int],
    zoom_scale: int,
    path: Path,
) -> None:
    full = Image.fromarray(source_rgb)
    annotate(full, bbox_xyxy, positives, negatives)
    cx1, cy1, cx2, cy2 = crop
    zoomed = Image.fromarray(source_rgb[cy1:cy2, cx1:cx2]).resize(
        ((cx2 - cx1) * zoom_scale, (cy2 - cy1) * zoom_scale), Image.NEAREST
    )
    annotate(zoomed, bbox_xyxy, positives, negatives, offset=(cx1, cy1), scale=zoom_scale)
    compose_panels(full, zoomed).save(path)


def mask_boundary(mask: np.ndarray) -> np.ndarray:
    image = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    eroded = np.asarray(image.filter(ImageFilter.MinFilter(3))) > 127
    return mask.astype(bool) & ~eroded


def save_mask_overlay(
    source_rgb: np.ndarray,
    mask: np.ndarray,
    bbox_xyxy: tuple[int, int, int, int],
    positives: list[tuple[int, int]],
    negatives: list[tuple[int, int]],
    crop: tuple[int, int, int, int],
    zoom_scale: int,
    alpha: float,
    path: Path,
) -> None:
    overlay = source_rgb.astype(np.float32).copy()
    region = mask.astype(bool)
    color = np.array(OVERLAY_COLOR, dtype=np.float32)
    overlay[region] = overlay[region] * (1.0 - alpha) + color * alpha
    overlay[mask_boundary(mask)] = OVERLAY_OUTLINE_COLOR
    overlay_rgb = overlay.astype(np.uint8)

    full = Image.fromarray(overlay_rgb)
    annotate(full, bbox_xyxy, positives, negatives)
    cx1, cy1, cx2, cy2 = crop
    zoomed = Image.fromarray(overlay_rgb[cy1:cy2, cx1:cx2]).resize(
        ((cx2 - cx1) * zoom_scale, (cy2 - cy1) * zoom_scale), Image.NEAREST
    )
    annotate(zoomed, bbox_xyxy, positives, negatives, offset=(cx1, cy1), scale=zoom_scale)
    compose_panels(full, zoomed).save(path)


def predict_case(
    predictor,
    bbox_xyxy: tuple[int, int, int, int],
    positive_points: list[tuple[int, int]],
    negative_points: list[tuple[int, int]],
) -> tuple[np.ndarray, np.ndarray, float]:
    """One SAM1 predict call; case 1 goes through the frozen ``predict_box`` path."""

    if not positive_points and not negative_points:
        start = time.perf_counter()
        masks, scores = sam_backend.predict_box(predictor, bbox_xyxy)
        elapsed = time.perf_counter() - start
        return masks, scores, elapsed

    point_coords = list(positive_points) + list(negative_points)
    point_labels = [1] * len(positive_points) + [0] * len(negative_points)
    start = time.perf_counter()
    masks, scores, _ = predictor.predict(
        point_coords=np.asarray(point_coords, dtype=np.float32),
        point_labels=np.asarray(point_labels, dtype=np.int64),
        box=np.asarray(bbox_xyxy, dtype=np.float32),
        multimask_output=True,
    )
    elapsed = time.perf_counter() - start
    return np.asarray(masks).astype(bool), np.asarray(scores, dtype=np.float64), elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path, help="source image path")
    parser.add_argument("--bbox", required=True, nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                        help="bbox in source XYWH pixels")
    parser.add_argument("--positive-points", nargs="*", type=parse_point, default=[],
                        help="positive points in source XY, e.g. 940,279 919,324")
    parser.add_argument("--negative-points", nargs="*", type=parse_point, default=[],
                        help="negative points in source XY, e.g. 897,316")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"))
    parser.add_argument("--out", required=True, type=Path, help="run output directory")
    parser.add_argument("--overlay-alpha", type=float, default=0.45)
    parser.add_argument("--zoom-pad", type=int, default=32)
    parser.add_argument("--zoom-scale", type=int, default=3)
    args = parser.parse_args()

    bbox_xywh = {"x": args.bbox[0], "y": args.bbox[1], "width": args.bbox[2], "height": args.bbox[3]}
    bbox_xyxy = (
        bbox_xywh["x"],
        bbox_xywh["y"],
        bbox_xywh["x"] + bbox_xywh["width"],
        bbox_xywh["y"] + bbox_xywh["height"],
    )

    with Image.open(args.source) as image:
        width, height = image.size
        source_rgb = np.asarray(image.convert("RGB"))
    if not (0 <= bbox_xyxy[0] < bbox_xyxy[2] <= width and 0 <= bbox_xyxy[1] < bbox_xyxy[3] <= height):
        raise SystemExit(f"bbox {bbox_xywh} is outside the {width}x{height} source image")
    for point in args.positive_points:
        check_in_bounds(point, width, height, "positive")
    for point in args.negative_points:
        check_in_bounds(point, width, height, "negative")

    cases = build_cases(args.positive_points, args.negative_points)
    args.out.mkdir(parents=True, exist_ok=True)

    predictor, predictor_info = sam_backend.load_sam_predictor("vit_b", str(args.checkpoint), args.device)

    start = time.perf_counter()
    sam_backend.encode_source(predictor, source_rgb)
    encode_seconds = time.perf_counter() - start

    # Throwaway call so the first CUDA kernels do not skew case 1 timing.
    start = time.perf_counter()
    predictor.predict(box=np.asarray(bbox_xyxy, dtype=np.float32), multimask_output=True)
    warmup_seconds = time.perf_counter() - start

    crop = zoom_crop(bbox_xyxy, width, height, args.zoom_pad)
    save_debug_image(
        source_rgb, bbox_xyxy, args.positive_points, args.negative_points,
        crop, args.zoom_scale, args.out / DEBUG_OUTPUT,
    )

    case_records = []
    for case in cases:
        masks, scores, predict_seconds = predict_case(
            predictor, bbox_xyxy, case["positive_points"], case["negative_points"]
        )
        winner = sam_backend.select_winner(scores)
        winner_mask = masks[winner]

        start = time.perf_counter()
        final_mask, postprocess_stats = sam_backend.postprocess_sam_mask(
            winner_mask, positive_points=case["positive_points"]
        )
        postprocess_seconds = time.perf_counter() - start

        save_mask_overlay(
            source_rgb, final_mask, bbox_xyxy,
            case["positive_points"], case["negative_points"],
            crop, args.zoom_scale, args.overlay_alpha, args.out / case["output"],
        )
        component_stats = postprocess_stats["connected_components"]
        case_records.append(
            {
                "name": case["name"],
                "description": case["description"],
                "points_used_source": {
                    "positive": [list(p) for p in case["positive_points"]],
                    "negative": [list(p) for p in case["negative_points"]],
                },
                "point_labels_sent": [1] * len(case["positive_points"]) + [0] * len(case["negative_points"]),
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
                "mask_output": case["output"],
            }
        )
        print(
            f"[{case['name']}] winner={winner} score={scores[winner]:.6f} "
            f"raw_area={int(winner_mask.sum())} final_area={int(final_mask.sum())} "
            f"predict={predict_seconds:.3f}s"
        )

    result = {
        "experiment": EXPERIMENT_ID,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "frozen_files_read_only": [
                "game-ui-asset-extractor/scripts/sam_backend.py",
                "game-ui-asset-extractor/scripts/extract_assets.py",
            ],
            "reused_from_frozen_backend": [
                "load_sam_predictor",
                "encode_source",
                "predict_box",
                "select_winner",
                "postprocess_sam_mask",
                "build_candidates_metadata",
            ],
            "roi_strategy": "whole-image set_image, encoded once per run (unchanged frozen strategy)",
        },
        "source_image": str(args.source.resolve()),
        "image_size": {"width": width, "height": height},
        "checkpoint": str(args.checkpoint.resolve()),
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
        "bbox_source": bbox_xywh,
        "bbox_source_xyxy": list(bbox_xyxy),
        "positive_points_source": [list(p) for p in args.positive_points],
        "negative_points_source": [list(p) for p in args.negative_points],
        "case_ladder": {case["name"]: case["description"] for case in cases},
        "timing": {
            "image_encode_seconds": encode_seconds,
            "warmup_predict_seconds": warmup_seconds,
        },
        "cases": case_records,
    }
    (args.out / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"done: {args.out}")


if __name__ == "__main__":
    main()
