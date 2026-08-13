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
METHOD = "local-foreground-v0.2"
SCHEMA_VERSION = "0.2"

# Explicit v0.1 reasonability limits. They intentionally reject large semantic jumps.
MAX_CENTER_SHIFT_FACTOR = 0.75
MIN_AREA_RATIO = 0.12
MAX_AREA_RATIO = 2.75
MIN_FOREGROUND_SUPPORT = 0.06
MIN_COLOR_DISTANCE = 18.0
BORDER_THICKNESS = 3
MIN_COMPONENT_PIXELS = 4
MERGE_DISTANCE_FACTOR = 0.25

# Final conservative acceptance gate for icon refinements. A rejected result is
# still observable, but downstream users must retain the original coarse bbox.
ACCEPT_MIN_AREA_RATIO = 0.60
ACCEPT_MAX_AREA_RATIO = 1.50
ACCEPT_MAX_CENTER_SHIFT_PX = 10.0
ACCEPT_MIN_DIMENSION_RATIO = 0.60
ACCEPT_MAX_DIMENSION_RATIO = 1.50


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


def _relevant_components(
    components: list[dict[str, Any]],
    coarse_local: dict[str, int],
) -> list[dict[str, Any]]:
    """Filter small or remote components without choosing a single winner."""

    coarse_area = coarse_local["width"] * coarse_local["height"]
    min_area = max(MIN_COMPONENT_PIXELS, round(coarse_area * 0.006))
    scale = max(coarse_local["width"], coarse_local["height"])
    coarse_center = bbox_center(coarse_local)
    relevant = []
    for component in components:
        if component["area"] < min_area:
            continue
        overlap = _bbox_intersection_area(component["bbox"], coarse_local)
        center_distance = math.dist(component["center"], coarse_center)
        if overlap > 0 or center_distance <= scale * 0.75:
            relevant.append(component)
    return relevant


def generate_component_groups(
    components: list[dict[str, Any]],
    coarse_local: dict[str, int],
) -> list[list[dict[str, Any]]]:
    """Generate deterministic single-component and progressively merged groups."""

    relevant = _relevant_components(components, coarse_local)
    if not relevant:
        return []

    scale = max(coarse_local["width"], coarse_local["height"])
    merge_distance = max(3.0, scale * MERGE_DISTANCE_FACTOR)
    vicinity = expand_bbox(
        coarse_local,
        (
            coarse_local["x"] + coarse_local["width"] + math.ceil(merge_distance),
            coarse_local["y"] + coarse_local["height"] + math.ceil(merge_distance),
        ),
        math.ceil(merge_distance),
    )
    component_order = {id(component): index for index, component in enumerate(relevant)}
    groups_by_bbox: dict[tuple[int, int, int, int], list[dict[str, Any]]] = {}

    def add_group(group: list[dict[str, Any]]) -> None:
        group_bbox = _union_bbox([component["bbox"] for component in group])
        key = (
            group_bbox["x"],
            group_bbox["y"],
            group_bbox["width"],
            group_bbox["height"],
        )
        existing = groups_by_bbox.get(key)
        if existing is None or sum(item["area"] for item in group) > sum(
            item["area"] for item in existing
        ):
            groups_by_bbox[key] = list(group)

    primaries = sorted(
        relevant,
        key=lambda item: (
            -_component_score(item, coarse_local),
            -item["area"],
            component_order[id(item)],
        ),
    )
    for primary in primaries:
        selected = [primary]
        add_group(selected)
        remaining = [component for component in relevant if component is not primary]
        while remaining:
            group_bbox = _union_bbox([component["bbox"] for component in selected])
            mergeable = [
                component
                for component in remaining
                if _bbox_gap(component["bbox"], group_bbox) <= merge_distance
                and _bbox_intersection_area(component["bbox"], vicinity) > 0
            ]
            if not mergeable:
                break
            next_component = min(
                mergeable,
                key=lambda item: (
                    _bbox_gap(item["bbox"], group_bbox),
                    -_component_score(item, coarse_local),
                    component_order[id(item)],
                ),
            )
            selected.append(next_component)
            remaining.remove(next_component)
            add_group(selected)

    return list(groups_by_bbox.values())


def select_component_group(
    components: list[dict[str, Any]],
    coarse_local: dict[str, int],
) -> list[dict[str, Any]]:
    """Compatibility helper returning the highest legacy-style component group."""

    groups = generate_component_groups(components, coarse_local)
    if not groups:
        return []
    return max(
        groups,
        key=lambda group: (
            _component_score(
                {
                    "bbox": _union_bbox([item["bbox"] for item in group]),
                    "center": bbox_center(_union_bbox([item["bbox"] for item in group])),
                    "area": sum(item["area"] for item in group),
                },
                coarse_local,
            ),
            sum(item["area"] for item in group),
        ),
    )


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


def _bbox_candidate_score(
    coarse_bbox: dict[str, int],
    candidate_bbox: dict[str, int],
) -> float:
    """Score icon geometry deterministically and strongly penalize oversize boxes."""

    coarse_area = coarse_bbox["width"] * coarse_bbox["height"]
    candidate_area = candidate_bbox["width"] * candidate_bbox["height"]
    area_ratio = candidate_area / coarse_area
    width_ratio = candidate_bbox["width"] / coarse_bbox["width"]
    height_ratio = candidate_bbox["height"] / coarse_bbox["height"]
    scale = max(coarse_bbox["width"], coarse_bbox["height"])
    center_distance = math.dist(bbox_center(candidate_bbox), bbox_center(coarse_bbox))
    center_score = max(0.0, 1.0 - center_distance / max(1.0, scale))

    intersection = _bbox_intersection_area(candidate_bbox, coarse_bbox)
    union = candidate_area + coarse_area - intersection
    overlap_score = intersection / max(1, union)
    area_score = math.exp(-abs(math.log(area_ratio)))
    dimension_score = (
        math.exp(-abs(math.log(width_ratio)))
        + math.exp(-abs(math.log(height_ratio)))
    ) / 2.0

    # The quadratic area term ensures a 50x36 candidate loses decisively to a
    # roughly coarse-sized icon candidate even if the larger region overlaps.
    oversize_penalty = max(0.0, area_ratio - 1.0) ** 2 * 6.0
    oversize_penalty += max(0.0, width_ratio - 1.25) ** 2 * 2.0
    oversize_penalty += max(0.0, height_ratio - 1.25) ** 2 * 2.0
    return round(
        center_score * 3.0
        + overlap_score * 3.0
        + area_score * 2.0
        + dimension_score * 2.0
        - oversize_penalty,
        8,
    )


def _rank_bbox_candidates(
    coarse_bbox: dict[str, int],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return candidates in stable best-first order."""

    for candidate in candidates:
        candidate["score"] = _bbox_candidate_score(coarse_bbox, candidate["bbox"])
    return sorted(
        candidates,
        key=lambda item: (
            -item["score"],
            math.dist(bbox_center(item["bbox"]), bbox_center(coarse_bbox)),
            abs(item["bbox"]["width"] * item["bbox"]["height"] - coarse_bbox["width"] * coarse_bbox["height"]),
            item["bbox"]["y"],
            item["bbox"]["x"],
            item["bbox"]["width"],
            item["bbox"]["height"],
        ),
    )


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


def _passes_icon_acceptance_gate(
    coarse_bbox: dict[str, int],
    refined_bbox: dict[str, int],
    metrics: dict[str, float],
) -> bool:
    """Accept only conservative icon bbox changes that are likely safer than coarse."""

    width_ratio = refined_bbox["width"] / coarse_bbox["width"]
    height_ratio = refined_bbox["height"] / coarse_bbox["height"]
    return (
        ACCEPT_MIN_AREA_RATIO <= metrics["area_ratio"] <= ACCEPT_MAX_AREA_RATIO
        and metrics["center_shift_px"] <= ACCEPT_MAX_CENTER_SHIFT_PX
        and ACCEPT_MIN_DIMENSION_RATIO <= width_ratio <= ACCEPT_MAX_DIMENSION_RATIO
        and ACCEPT_MIN_DIMENSION_RATIO <= height_ratio <= ACCEPT_MAX_DIMENSION_RATIO
    )


def _finalize_icon_result(
    base: dict[str, Any],
    coarse_bbox: dict[str, int],
    refined_bbox: dict[str, int],
    metrics: dict[str, float],
    candidate_count: int = 1,
    selected_candidate_rank: int = 1,
) -> dict[str, Any]:
    """Choose refined or coarse after the conservative final acceptance gate."""

    if not _passes_icon_acceptance_gate(coarse_bbox, refined_bbox, metrics):
        return {
            **base,
            "refined_bbox": refined_bbox,
            "status": "fallback",
            "use_bbox": "coarse",
            "candidate_count": candidate_count,
            "selected_candidate_rank": None,
            "confidence": 0.0,
            "metrics": metrics,
            "failure_reason": "refined bbox rejected by acceptance gate",
        }
    return {
        **base,
        "refined_bbox": refined_bbox,
        "status": "success",
        "use_bbox": "refined",
        "candidate_count": candidate_count,
        "selected_candidate_rank": selected_candidate_rank,
        "confidence": _confidence(coarse_bbox, refined_bbox, metrics),
        "metrics": metrics,
    }


def _select_ranked_candidate(
    base: dict[str, Any],
    coarse_bbox: dict[str, int],
    ranked_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Try candidates in score order and retain coarse only if every gate fails."""

    candidate_count = len(ranked_candidates)
    for rank, candidate in enumerate(ranked_candidates, start=1):
        metrics = candidate["metrics"]
        if _failure_reason(coarse_bbox, candidate["bbox"], metrics) is not None:
            continue
        if _passes_icon_acceptance_gate(coarse_bbox, candidate["bbox"], metrics):
            return _finalize_icon_result(
                base,
                coarse_bbox,
                candidate["bbox"],
                metrics,
                candidate_count,
                rank,
            )

    best = ranked_candidates[0]
    return {
        **base,
        "refined_bbox": best["bbox"],
        "status": "fallback",
        "use_bbox": "coarse",
        "candidate_count": candidate_count,
        "selected_candidate_rank": None,
        "confidence": 0.0,
        "metrics": best["metrics"],
        "failure_reason": "all refined bbox candidates rejected by acceptance gate",
    }


def refine_icon(
    source_rgb: np.ndarray,
    asset: dict[str, Any],
    expand_px: int | None = None,
    safety_padding: int = 2,
    debug_candidates_out: list[dict[str, int]] | None = None,
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
    component_groups = generate_component_groups(components, coarse_local)
    base = {
        "asset_id": asset["id"],
        "coarse_bbox": coarse_bbox,
        "roi_bbox": roi_bbox,
    }
    if not component_groups:
        return {
            **base,
            "refined_bbox": None,
            "status": "failed",
            "use_bbox": "coarse",
            "candidate_count": 0,
            "selected_candidate_rank": None,
            "confidence": 0.0,
            "metrics": None,
            "failure_reason": "no relevant foreground component detected",
        }, mask

    bbox_candidates: list[dict[str, Any]] = []
    for group in component_groups:
        tight_local = _union_bbox([component["bbox"] for component in group])
        candidate_bbox = _clamp_and_pad_bbox(
            tight_local,
            roi_bbox,
            (image_width, image_height),
            safety_padding,
        )
        selected_pixels = sum(component["area"] for component in group)
        bbox_candidates.append(
            {
                "bbox": candidate_bbox,
                "metrics": _metrics(coarse_bbox, candidate_bbox, selected_pixels),
            }
        )
    ranked_candidates = _rank_bbox_candidates(coarse_bbox, bbox_candidates)
    if debug_candidates_out is not None:
        debug_candidates_out.extend(dict(candidate["bbox"]) for candidate in ranked_candidates)
    return _select_ranked_candidate(base, coarse_bbox, ranked_candidates), mask


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
        "use_bbox": "coarse",
        "candidate_count": 0,
        "selected_candidate_rank": None,
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
    candidate_bboxes: list[dict[str, int]],
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
    if result["use_bbox"] == "refined" and result["refined_bbox"] is not None:
        fx1, fy1, fx2, fy2 = bbox_edges(result["refined_bbox"])
        draw.rectangle((fx1, fy1, fx2 - 1, fy2 - 1), outline=(0, 255, 80), width=2)
    overlay.save(debug_dir / f"{asset_id}-overlay.png")

    candidate_overlay = source_image.convert("RGB").copy()
    candidate_draw = ImageDraw.Draw(candidate_overlay)
    candidate_draw.rectangle((cx1, cy1, cx2 - 1, cy2 - 1), outline=(255, 215, 0), width=2)
    candidate_draw.rectangle((x1, y1, x2 - 1, y2 - 1), outline=(0, 160, 255), width=2)
    candidate_colors = [(255, 0, 255), (255, 128, 0), (0, 255, 255)]
    for rank, (candidate_bbox, color) in enumerate(
        zip(candidate_bboxes[:3], candidate_colors),
        start=1,
    ):
        bx1, by1, bx2, by2 = bbox_edges(candidate_bbox)
        candidate_draw.rectangle((bx1, by1, bx2 - 1, by2 - 1), outline=color, width=1)
        candidate_draw.text((bx1 + 1, by1 + 1), str(rank), fill=color)
    if result["use_bbox"] == "refined" and result["refined_bbox"] is not None:
        fx1, fy1, fx2, fy2 = bbox_edges(result["refined_bbox"])
        candidate_draw.rectangle(
            (fx1, fy1, fx2 - 1, fy2 - 1),
            outline=(0, 255, 80),
            width=3,
        )
    candidate_overlay.save(debug_dir / f"{asset_id}-candidates.png")


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
        debug_candidates: list[dict[str, int]] = []
        result, mask = refine_icon(
            source_rgb,
            asset,
            expand_px,
            safety_padding,
            debug_candidates,
        )
        refinements.append(result)
        if debug_dir is not None:
            _write_debug(
                debug_dir,
                source_pil,
                asset["id"],
                result,
                mask,
                debug_candidates,
            )

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
    fallbacks = sum(item["status"] == "fallback" for item in document["refinements"])
    failures = sum(item["status"] == "failed" for item in document["refinements"])
    skipped = sum(item["status"] == "skipped" for item in document["refinements"])
    print(
        f"Wrote {args.output}: {successes} success, {fallbacks} fallback, "
        f"{failures} failed, {skipped} skipped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
