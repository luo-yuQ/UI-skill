#!/usr/bin/env python3
"""Refine coarse direct-crop icon bboxes with deterministic local pixel analysis."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw, UnidentifiedImageError

from validate_asset_analysis import validate_analysis


ROOT = Path(__file__).resolve().parents[1]
REFINEMENT_SCHEMA_PATH = ROOT / "schemas" / "bbox-refinement.schema.json"
METHOD = "local-foreground-v0.1"
SCHEMA_VERSION = "0.1"

# Explicit v0.1 reasonability limits. They intentionally reject large semantic jumps.
MAX_CENTER_SHIFT_FACTOR = 0.75
MIN_AREA_RATIO = 0.12
MAX_AREA_RATIO = 2.75
MIN_FOREGROUND_SUPPORT = 0.06
MIN_COLOR_DISTANCE = 18.0
BORDER_THICKNESS = 3
MIN_COMPONENT_PIXELS = 4
MERGE_DISTANCE_FACTOR = 0.25


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def bbox_edges(bbox: dict[str, int]) -> tuple[int, int, int, int]:
    return (
        bbox["x"],
        bbox["y"],
        bbox["x"] + bbox["width"],
        bbox["y"] + bbox["height"],
    )


def bbox_center(bbox: dict[str, int]) -> tuple[float, float]:
    return (
        bbox["x"] + bbox["width"] / 2.0,
        bbox["y"] + bbox["height"] / 2.0,
    )


def expand_bbox(
    bbox: dict[str, int],
    image_size: tuple[int, int],
    expand_px: int | None = None,
) -> dict[str, int]:
    """Expand a bbox by the deterministic default or explicit pixels, then clamp."""

    image_width, image_height = image_size
    expansion = (
        max(8, round(max(bbox["width"], bbox["height"]) * 0.45))
        if expand_px is None
        else expand_px
    )
    if expansion < 0:
        raise ValueError("expand_px must be >= 0")
    x1 = max(0, bbox["x"] - expansion)
    y1 = max(0, bbox["y"] - expansion)
    x2 = min(image_width, bbox["x"] + bbox["width"] + expansion)
    y2 = min(image_height, bbox["y"] + bbox["height"] + expansion)
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _border_pixels(rgb: np.ndarray, thickness: int = BORDER_THICKNESS) -> np.ndarray:
    height, width, _ = rgb.shape
    thickness = max(1, min(thickness, height, width))
    border_mask = np.zeros((height, width), dtype=bool)
    border_mask[:thickness, :] = True
    border_mask[-thickness:, :] = True
    border_mask[:, :thickness] = True
    border_mask[:, -thickness:] = True
    return rgb[border_mask]


def foreground_mask(rgb: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """Estimate local background from ROI borders and return an adaptive color mask."""

    pixels = rgb.astype(np.float32)
    border = _border_pixels(pixels)
    background = np.median(border, axis=0)
    distance = np.linalg.norm(pixels - background, axis=2)
    border_distance = np.linalg.norm(border - background, axis=1)

    # Use robust spread rather than a high percentile: an icon touching a source
    # edge can legitimately occupy part of the ROI border and must not become
    # the value that defines background noise.
    border_median = float(np.median(border_distance))
    border_mad = float(np.median(np.abs(border_distance - border_median)))
    background_noise = border_median + border_mad * 3.0
    median_distance = float(np.median(distance))
    high_distance = float(np.percentile(distance, 90))
    adaptive_delta = max(8.0, (high_distance - median_distance) * 0.35)
    threshold = max(
        MIN_COLOR_DISTANCE,
        background_noise + 8.0,
        median_distance + adaptive_delta,
    )
    mask = distance >= threshold
    return mask, {
        "background_r": float(background[0]),
        "background_g": float(background[1]),
        "background_b": float(background[2]),
        "color_threshold": threshold,
    }


def connected_components(mask: np.ndarray) -> list[dict[str, Any]]:
    """Return 8-connected foreground components using a lightweight flood fill."""

    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    components: list[dict[str, Any]] = []
    for start_y in range(height):
        for start_x in range(width):
            if not mask[start_y, start_x] or visited[start_y, start_x]:
                continue
            queue: deque[tuple[int, int]] = deque([(start_x, start_y)])
            visited[start_y, start_x] = True
            xs: list[int] = []
            ys: list[int] = []
            while queue:
                x, y = queue.popleft()
                xs.append(x)
                ys.append(y)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if (
                            0 <= nx < width
                            and 0 <= ny < height
                            and mask[ny, nx]
                            and not visited[ny, nx]
                        ):
                            visited[ny, nx] = True
                            queue.append((nx, ny))
            area = len(xs)
            components.append(
                {
                    "area": area,
                    "bbox": {
                        "x": min(xs),
                        "y": min(ys),
                        "width": max(xs) - min(xs) + 1,
                        "height": max(ys) - min(ys) + 1,
                    },
                    "center": (sum(xs) / area, sum(ys) / area),
                }
            )
    return components


def _bbox_intersection_area(a: dict[str, int], b: dict[str, int]) -> int:
    ax1, ay1, ax2, ay2 = bbox_edges(a)
    bx1, by1, bx2, by2 = bbox_edges(b)
    return max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )


def _bbox_gap(a: dict[str, int], b: dict[str, int]) -> float:
    ax1, ay1, ax2, ay2 = bbox_edges(a)
    bx1, by1, bx2, by2 = bbox_edges(b)
    dx = max(0, max(ax1, bx1) - min(ax2, bx2))
    dy = max(0, max(ay1, by1) - min(ay2, by2))
    return math.hypot(dx, dy)


def _union_bbox(boxes: list[dict[str, int]]) -> dict[str, int]:
    edges = [bbox_edges(box) for box in boxes]
    x1 = min(edge[0] for edge in edges)
    y1 = min(edge[1] for edge in edges)
    x2 = max(edge[2] for edge in edges)
    y2 = max(edge[3] for edge in edges)
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _component_score(
    component: dict[str, Any],
    coarse_local: dict[str, int],
) -> float:
    coarse_area = coarse_local["width"] * coarse_local["height"]
    overlap = _bbox_intersection_area(component["bbox"], coarse_local)
    overlap_ratio = overlap / max(1, component["bbox"]["width"] * component["bbox"]["height"])
    coarse_center = bbox_center(coarse_local)
    distance = math.dist(component["center"], coarse_center)
    scale = max(coarse_local["width"], coarse_local["height"])
    proximity = max(0.0, 1.0 - distance / max(1.0, scale * 1.5))
    area_fit = min(component["area"] / max(1, coarse_area), 1.0)
    return overlap_ratio * 4.0 + proximity * 2.0 + math.sqrt(area_fit) * 0.5


def select_component_group(
    components: list[dict[str, Any]],
    coarse_local: dict[str, int],
) -> list[dict[str, Any]]:
    """Select a relevant primary component and deterministic nearby companions."""

    coarse_area = coarse_local["width"] * coarse_local["height"]
    min_area = max(MIN_COMPONENT_PIXELS, round(coarse_area * 0.006))
    scale = max(coarse_local["width"], coarse_local["height"])
    coarse_center = bbox_center(coarse_local)
    candidates = []
    for component in components:
        if component["area"] < min_area:
            continue
        overlap = _bbox_intersection_area(component["bbox"], coarse_local)
        center_distance = math.dist(component["center"], coarse_center)
        if overlap > 0 or center_distance <= scale * 0.75:
            candidates.append(component)
    if not candidates:
        return []

    primary = max(candidates, key=lambda item: (_component_score(item, coarse_local), item["area"]))
    selected = [primary]
    remaining = [component for component in candidates if component is not primary]
    merge_distance = max(3.0, scale * MERGE_DISTANCE_FACTOR)
    vicinity = expand_bbox(
        coarse_local,
        (
            coarse_local["x"] + coarse_local["width"] + math.ceil(merge_distance),
            coarse_local["y"] + coarse_local["height"] + math.ceil(merge_distance),
        ),
        math.ceil(merge_distance),
    )

    changed = True
    while changed:
        changed = False
        group_bbox = _union_bbox([component["bbox"] for component in selected])
        for component in list(remaining):
            if (
                _bbox_gap(component["bbox"], group_bbox) <= merge_distance
                and _bbox_intersection_area(component["bbox"], vicinity) > 0
            ):
                selected.append(component)
                remaining.remove(component)
                changed = True
    return selected


def _clamp_and_pad_bbox(
    local_bbox: dict[str, int],
    roi_bbox: dict[str, int],
    image_size: tuple[int, int],
    padding: int,
) -> dict[str, int]:
    image_width, image_height = image_size
    x1 = max(0, roi_bbox["x"] + local_bbox["x"] - padding)
    y1 = max(0, roi_bbox["y"] + local_bbox["y"] - padding)
    x2 = min(
        image_width,
        roi_bbox["x"] + local_bbox["x"] + local_bbox["width"] + padding,
    )
    y2 = min(
        image_height,
        roi_bbox["y"] + local_bbox["y"] + local_bbox["height"] + padding,
    )
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _metrics(
    coarse_bbox: dict[str, int],
    refined_bbox: dict[str, int],
    selected_pixels: int,
) -> dict[str, float]:
    coarse_area = coarse_bbox["width"] * coarse_bbox["height"]
    refined_area = refined_bbox["width"] * refined_bbox["height"]
    return {
        "center_shift_px": round(math.dist(bbox_center(coarse_bbox), bbox_center(refined_bbox)), 4),
        "area_ratio": round(refined_area / coarse_area, 6),
        "foreground_pixel_ratio": round(min(1.0, selected_pixels / refined_area), 6),
    }


def _confidence(
    coarse_bbox: dict[str, int],
    refined_bbox: dict[str, int],
    metrics: dict[str, float],
) -> float:
    scale = max(coarse_bbox["width"], coarse_bbox["height"])
    shift_score = max(0.0, 1.0 - metrics["center_shift_px"] / (scale * MAX_CENTER_SHIFT_FACTOR))
    area_score = math.exp(-abs(math.log(metrics["area_ratio"])))
    support_score = min(1.0, metrics["foreground_pixel_ratio"] / 0.55)
    intersection = _bbox_intersection_area(coarse_bbox, refined_bbox)
    overlap_score = intersection / max(1, refined_bbox["width"] * refined_bbox["height"])
    return round(
        min(1.0, 0.4 * shift_score + 0.25 * area_score + 0.2 * support_score + 0.15 * overlap_score),
        4,
    )


def _failure_reason(
    coarse_bbox: dict[str, int],
    refined_bbox: dict[str, int],
    metrics: dict[str, float],
) -> str | None:
    if refined_bbox["width"] <= 0 or refined_bbox["height"] <= 0:
        return "refined bbox has non-positive dimensions"
    scale = max(coarse_bbox["width"], coarse_bbox["height"])
    if metrics["center_shift_px"] > scale * MAX_CENTER_SHIFT_FACTOR:
        return "refined center is too far from the coarse center"
    if metrics["area_ratio"] < MIN_AREA_RATIO:
        return "refined area is too small relative to the coarse bbox"
    if metrics["area_ratio"] > MAX_AREA_RATIO:
        return "refined area is too large relative to the coarse bbox"
    if metrics["foreground_pixel_ratio"] < MIN_FOREGROUND_SUPPORT:
        return "foreground support is too weak"
    return None


def refine_icon(
    source_rgb: np.ndarray,
    asset: dict[str, Any],
    expand_px: int | None = None,
    safety_padding: int = 2,
) -> tuple[dict[str, Any], np.ndarray]:
    """Refine one eligible icon and return its result plus ROI mask."""

    if safety_padding < 0:
        raise ValueError("safety_padding must be >= 0")
    image_height, image_width, _ = source_rgb.shape
    coarse_bbox = dict(asset["bbox"])
    roi_bbox = expand_bbox(coarse_bbox, (image_width, image_height), expand_px)
    rx, ry, rw, rh = (
        roi_bbox["x"],
        roi_bbox["y"],
        roi_bbox["width"],
        roi_bbox["height"],
    )
    roi = source_rgb[ry : ry + rh, rx : rx + rw]
    mask, _diagnostics = foreground_mask(roi)
    components = connected_components(mask)
    coarse_local = {
        "x": coarse_bbox["x"] - rx,
        "y": coarse_bbox["y"] - ry,
        "width": coarse_bbox["width"],
        "height": coarse_bbox["height"],
    }
    selected = select_component_group(components, coarse_local)
    base = {
        "asset_id": asset["id"],
        "coarse_bbox": coarse_bbox,
        "roi_bbox": roi_bbox,
    }
    if not selected:
        return {
            **base,
            "refined_bbox": None,
            "status": "failed",
            "confidence": 0.0,
            "metrics": None,
            "failure_reason": "no relevant foreground component detected",
        }, mask

    tight_local = _union_bbox([component["bbox"] for component in selected])
    refined_bbox = _clamp_and_pad_bbox(
        tight_local,
        roi_bbox,
        (image_width, image_height),
        safety_padding,
    )
    selected_pixels = sum(component["area"] for component in selected)
    metrics = _metrics(coarse_bbox, refined_bbox, selected_pixels)
    failure = _failure_reason(coarse_bbox, refined_bbox, metrics)
    if failure is not None:
        return {
            **base,
            "refined_bbox": None,
            "status": "failed",
            "confidence": 0.0,
            "metrics": metrics,
            "failure_reason": failure,
        }, mask
    return {
        **base,
        "refined_bbox": refined_bbox,
        "status": "success",
        "confidence": _confidence(coarse_bbox, refined_bbox, metrics),
        "metrics": metrics,
    }, mask


def is_eligible(asset: dict[str, Any]) -> bool:
    return (
        asset.get("should_extract") is True
        and asset.get("strategy") == "direct_crop"
        and asset.get("semantic_type") == "icon"
    )


def _skipped(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": asset["id"],
        "coarse_bbox": dict(asset["bbox"]),
        "roi_bbox": None,
        "refined_bbox": None,
        "status": "skipped",
        "confidence": 0.0,
        "metrics": None,
        "failure_reason": "v0.1 supports only direct_crop icons with should_extract=true",
    }


def _write_debug(
    debug_dir: Path,
    source_image: Image.Image,
    asset_id: str,
    result: dict[str, Any],
    mask: np.ndarray,
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    roi_bbox = result["roi_bbox"]
    if roi_bbox is None:
        return
    x1, y1, x2, y2 = bbox_edges(roi_bbox)
    source_image.crop((x1, y1, x2, y2)).save(debug_dir / f"{asset_id}-roi.png")
    Image.fromarray((mask.astype(np.uint8) * 255), mode="L").save(
        debug_dir / f"{asset_id}-mask.png"
    )

    overlay = source_image.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    cx1, cy1, cx2, cy2 = bbox_edges(result["coarse_bbox"])
    draw.rectangle((cx1, cy1, cx2 - 1, cy2 - 1), outline=(255, 215, 0), width=2)
    draw.rectangle((x1, y1, x2 - 1, y2 - 1), outline=(0, 160, 255), width=2)
    if result["refined_bbox"] is not None:
        fx1, fy1, fx2, fy2 = bbox_edges(result["refined_bbox"])
        draw.rectangle((fx1, fy1, fx2 - 1, fy2 - 1), outline=(0, 255, 80), width=2)
    overlay.save(debug_dir / f"{asset_id}-overlay.png")


def validate_refinement(data: Any) -> list[str]:
    try:
        schema = load_json(REFINEMENT_SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"unable to load refinement schema: {exc}"]
    return [error.message for error in Draft202012Validator(schema).iter_errors(data)]


def refine_document(
    source_image: Path,
    analysis: dict[str, Any],
    ids: set[str] | None = None,
    expand_px: int | None = None,
    safety_padding: int = 2,
    debug_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a standalone refinement artifact without mutating asset analysis."""

    try:
        with Image.open(source_image) as opened:
            opened.load()
            source_pil = opened.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read source image {source_image}: {exc}") from exc

    analysis_errors = validate_analysis(analysis, source_image)
    if analysis_errors:
        raise ValueError("invalid asset analysis:\n- " + "\n- ".join(analysis_errors))

    source_width, source_height = source_pil.size
    expected_size = {"width": source_width, "height": source_height}
    if analysis.get("source_image") != source_image.name:
        raise ValueError("asset analysis source_image does not match the source image file name")
    if analysis.get("source_size") != expected_size:
        raise ValueError("asset analysis source_size does not match the real source image")
    assets = analysis.get("assets")
    if not isinstance(assets, list):
        raise ValueError("asset analysis assets must be an array")
    by_id = {asset.get("id"): asset for asset in assets if isinstance(asset, dict)}
    if ids is not None:
        missing = sorted(ids - set(by_id))
        if missing:
            raise ValueError("requested asset ids do not exist: " + ", ".join(missing))
        selected_assets = [asset for asset in assets if asset.get("id") in ids]
    else:
        selected_assets = assets

    source_rgb = np.asarray(source_pil)
    refinements: list[dict[str, Any]] = []
    for asset in selected_assets:
        if not is_eligible(asset):
            refinements.append(_skipped(asset))
            continue
        result, mask = refine_icon(source_rgb, asset, expand_px, safety_padding)
        refinements.append(result)
        if debug_dir is not None:
            _write_debug(debug_dir, source_pil, asset["id"], result, mask)

    document = {
        "schema_version": SCHEMA_VERSION,
        "source_image": source_image.name,
        "source_size": expected_size,
        "method": METHOD,
        "refinements": refinements,
    }
    errors = validate_refinement(document)
    if errors:
        raise ValueError("invalid bbox refinement:\n- " + "\n- ".join(errors))
    return document


def parse_ids(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    ids = {item.strip() for item in raw.split(",") if item.strip()}
    if not ids:
        raise ValueError("--ids must contain at least one asset id")
    return ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refine direct-crop icon bboxes with deterministic local foreground analysis."
    )
    parser.add_argument("--source-image", required=True, type=Path)
    parser.add_argument("--asset-analysis", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--debug-dir", type=Path)
    parser.add_argument("--expand-px", type=int)
    parser.add_argument("--safety-padding", type=int, default=2)
    parser.add_argument("--ids", help="comma-separated asset IDs to process")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        analysis = load_json(args.asset_analysis)
        if not isinstance(analysis, dict):
            raise ValueError("asset analysis root must be an object")
        document = refine_document(
            args.source_image,
            analysis,
            parse_ids(args.ids),
            args.expand_px,
            args.safety_padding,
            args.debug_dir,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except json.JSONDecodeError as exc:
        print(f"Refinement failed: invalid JSON: {exc}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Refinement failed: {exc}", file=sys.stderr)
        return 1
    successes = sum(item["status"] == "success" for item in document["refinements"])
    failures = sum(item["status"] == "failed" for item in document["refinements"])
    skipped = sum(item["status"] == "skipped" for item in document["refinements"])
    print(
        f"Wrote {args.output}: {successes} success, {failures} failed, {skipped} skipped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
