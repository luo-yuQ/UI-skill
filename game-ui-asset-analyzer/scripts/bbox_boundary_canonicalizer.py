#!/usr/bin/env python3
"""Deterministically clamp tiny Analysis Image boundary quantization errors."""

from __future__ import annotations

import copy
from typing import Any


BBOX_BOUNDARY_TOLERANCE_PX = 4
STRATEGY_BBOX_COLLECTIONS = {
    "structural_split": "children",
    "expand_instances": "instances",
    "semantic_decompose": "children",
}
BBOX_KEYS = ("x", "y", "width", "height")


def canonicalize_strategy_bboxes(
    document: dict[str, Any],
    *,
    strategy: str,
    image_size: tuple[int, int],
) -> list[dict[str, Any]]:
    """Clamp eligible bbox edges in-place and return traceable diagnostics.

    Invalid, excessive, non-intersecting, and already-valid bboxes are left
    untouched so the existing frozen validator remains the final authority.
    """

    collection_key = STRATEGY_BBOX_COLLECTIONS.get(strategy)
    collection = document.get(collection_key) if collection_key is not None else None
    if not isinstance(collection, list):
        return []
    image_width, image_height = image_size
    if (
        type(image_width) is not int
        or type(image_height) is not int
        or image_width <= 0
        or image_height <= 0
    ):
        return []

    canonicalizations: list[dict[str, Any]] = []
    for index, item in enumerate(collection):
        bbox = item.get("bbox") if isinstance(item, dict) else None
        if not isinstance(bbox, dict) or set(bbox) != set(BBOX_KEYS):
            continue
        values = [bbox.get(key) for key in BBOX_KEYS]
        if not all(type(value) is int for value in values):
            continue
        x, y, width, height = values
        if width <= 0 or height <= 0:
            continue

        raw_edges = {
            "left": x,
            "top": y,
            "right": x + width,
            "bottom": y + height,
        }
        if not (
            raw_edges["left"] >= -BBOX_BOUNDARY_TOLERANCE_PX
            and raw_edges["top"] >= -BBOX_BOUNDARY_TOLERANCE_PX
            and raw_edges["right"]
            <= image_width + BBOX_BOUNDARY_TOLERANCE_PX
            and raw_edges["bottom"]
            <= image_height + BBOX_BOUNDARY_TOLERANCE_PX
        ):
            continue

        canonical_edges = {
            "left": max(0, raw_edges["left"]),
            "top": max(0, raw_edges["top"]),
            "right": min(image_width, raw_edges["right"]),
            "bottom": min(image_height, raw_edges["bottom"]),
        }
        if (
            canonical_edges["right"] <= canonical_edges["left"]
            or canonical_edges["bottom"] <= canonical_edges["top"]
        ):
            continue

        adjustments = {
            edge: {
                "raw": raw_edges[edge],
                "canonical": canonical_edges[edge],
                "delta_px": canonical_edges[edge] - raw_edges[edge],
            }
            for edge in ("left", "top", "right", "bottom")
            if canonical_edges[edge] != raw_edges[edge]
        }
        if not adjustments:
            continue

        raw_bbox = copy.deepcopy(bbox)
        canonical_bbox = {
            "x": canonical_edges["left"],
            "y": canonical_edges["top"],
            "width": canonical_edges["right"] - canonical_edges["left"],
            "height": canonical_edges["bottom"] - canonical_edges["top"],
        }
        bbox.clear()
        bbox.update(canonical_bbox)
        diagnostic = {
            "path": f"$.{collection_key}[{index}].bbox",
            "raw_bbox": raw_bbox,
            "canonical_bbox": copy.deepcopy(canonical_bbox),
            "adjustments": adjustments,
        }
        item_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(item_id, str) and item_id:
            diagnostic["item_id"] = item_id
        canonicalizations.append(diagnostic)

    return canonicalizations
