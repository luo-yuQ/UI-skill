"""Shared deterministic geometry and contract helpers for Stage2-C.

This module contains no repair algorithm. It only defines:

- bbox predicates (validity, strict containment, intersection area);
- the source-pixel coordinate contract used by both C1 and C1.5;
- frame resolution: any extracted frame (final_bbox core or extraction_roi)
  maps to source pixels through a single ``frame_origin`` pair.
"""

from __future__ import annotations

from typing import Any

BBox = dict[str, int]


class InvalidBboxError(ValueError):
    """Raised when a bbox violates the Stage2-A/B source-pixel contract."""


def bbox_is_valid(bbox: Any) -> bool:
    """A bbox must be a dict of ints with x >= 0, y >= 0, width > 0, height > 0."""

    if not isinstance(bbox, dict):
        return False
    for key in ("x", "y", "width", "height"):
        value = bbox.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    return bbox["x"] >= 0 and bbox["y"] >= 0 and bbox["width"] > 0 and bbox["height"] > 0


def require_valid_bbox(bbox: Any, what: str) -> BBox:
    if not bbox_is_valid(bbox):
        raise InvalidBboxError(f"{what} is invalid: {bbox!r}")
    return bbox


def bbox_edges(bbox: BBox) -> tuple[int, int, int, int]:
    """Half-open edges (x1, y1, x2, y2) with x2 = x + width (exclusive)."""

    return (bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"])


def bbox_area(bbox: BBox) -> int:
    return int(bbox["width"]) * int(bbox["height"])


def contains_strict(large: BBox, small: BBox) -> bool:
    """True when small is entirely inside large: intersection_area == small_area.

    Equal bboxes also satisfy this (small == large is fully contained), but the
    relation builder never pairs an asset with itself.
    """

    lx1, ly1, lx2, ly2 = bbox_edges(large)
    sx1, sy1, sx2, sy2 = bbox_edges(small)
    return lx1 <= sx1 and ly1 <= sy1 and sx2 <= lx2 and sy2 <= ly2


def intersection_area(a: BBox, b: BBox) -> int:
    ax1, ay1, ax2, ay2 = bbox_edges(a)
    bx1, by1, bx2, by2 = bbox_edges(b)
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    return int(ix) * int(iy)


def frame_origin(frame: BBox) -> tuple[int, int]:
    """Origin of a local coordinate frame in source pixels."""

    return (int(frame["x"]), int(frame["y"]))


def local_to_source(frame: BBox, local_x: int, local_y: int) -> tuple[int, int]:
    fx, fy = frame_origin(frame)
    return (local_x + fx, local_y + fy)


def source_to_local(frame: BBox, source_x: int, source_y: int) -> tuple[int, int]:
    fx, fy = frame_origin(frame)
    return (source_x - fx, source_y - fy)


def target_frame_from_extraction_record(record: dict[str, Any]) -> BBox:
    """Resolve the target local frame from a Stage2-B extraction record.

    The extracted PNG lives inside ``extraction_roi`` when present (padded
    ROI-local coordinates); otherwise it is exactly ``final_bbox``
    (direct_crop / zero padding). This is the single place where the two
    Stage2-B output shapes are normalized for Stage2-C.
    """

    roi = record.get("extraction_roi")
    if roi is not None:
        return require_valid_bbox(roi, "extraction_roi")
    return require_valid_bbox(record.get("final_bbox"), "final_bbox")


def occluder_frame_from_extraction_record(record: dict[str, Any]) -> BBox:
    """Resolve the occluder mask frame the same way as the target frame."""

    return target_frame_from_extraction_record(record)
