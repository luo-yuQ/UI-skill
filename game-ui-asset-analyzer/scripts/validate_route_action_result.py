#!/usr/bin/env python3
"""Deterministically judge whether a routed Stage2-A action was effective."""

from __future__ import annotations

from collections import Counter
from typing import Any


PARENT_SIZED_RATIO = 0.90
MASS_DUPLICATE_RATIO = 0.50


def _result(valid: bool, reason_code: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "valid": valid,
        "reason_code": reason_code,
        "reasons": reasons,
    }


def _valid_parent_size(parent_size: Any) -> tuple[int, int] | None:
    if (
        not isinstance(parent_size, tuple)
        or len(parent_size) != 2
        or any(type(value) is not int or value <= 0 for value in parent_size)
    ):
        return None
    return parent_size


def _bbox_tuple(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, dict):
        return None
    values = tuple(value.get(key) for key in ("x", "y", "width", "height"))
    if any(type(item) is not int for item in values):
        return None
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _bbox_errors(
    items: list[Any], *, collection: str, parent_size: tuple[int, int]
) -> tuple[list[str], list[tuple[int, int, int, int]]]:
    parent_width, parent_height = parent_size
    errors: list[str] = []
    bboxes: list[tuple[int, int, int, int]] = []
    for index, item in enumerate(items):
        bbox = _bbox_tuple(item.get("bbox") if isinstance(item, dict) else None)
        if bbox is None:
            errors.append(f"{collection}[{index}].bbox is not a positive integer bbox")
            continue
        x, y, width, height = bbox
        if x + width > parent_width or y + height > parent_height:
            errors.append(
                f"{collection}[{index}].bbox exceeds parent bounds "
                f"{parent_width}x{parent_height}"
            )
        bboxes.append(bbox)
    return errors, bboxes


def validate_structural_split_result(
    result: Any, parent_size: tuple[int, int]
) -> dict[str, Any]:
    """Return structured effectiveness, separate from schema/contract validity."""

    size = _valid_parent_size(parent_size)
    if size is None:
        return _result(False, "INVALID_PARENT_SIZE", [f"parent_size={parent_size!r}"])
    if not isinstance(result, dict):
        return _result(
            False, "INVALID_STRUCTURAL_RESULT", ["result is not an object"]
        )

    children = result.get("children")
    if not isinstance(children, list):
        return _result(
            False,
            "INVALID_STRUCTURAL_CHILDREN",
            ["children is missing or is not an array"],
        )
    if result.get("no_useful_structural_split") is True:
        reasons = ["no_useful_structural_split=true"]
        if not children:
            reasons.append("children.length=0")
        return _result(False, "NO_USEFUL_STRUCTURAL_SPLIT", reasons)
    if not children:
        return _result(False, "EMPTY_STRUCTURAL_CHILDREN", ["children.length=0"])

    bbox_errors, bboxes = _bbox_errors(
        children, collection="children", parent_size=size
    )
    if bbox_errors:
        return _result(False, "INVALID_STRUCTURAL_CHILD_BBOX", bbox_errors)
    duplicates = sorted(bbox for bbox, count in Counter(bboxes).items() if count > 1)
    if duplicates:
        return _result(
            False,
            "DUPLICATE_STRUCTURAL_CHILD_BBOX",
            [f"duplicate child bbox={bbox}" for bbox in duplicates],
        )

    parent_width, parent_height = size
    if all(
        width / parent_width >= PARENT_SIZED_RATIO
        and height / parent_height >= PARENT_SIZED_RATIO
        for _x, _y, width, height in bboxes
    ):
        return _result(
            False,
            "INEFFECTIVE_PARENT_SIZED_CHILDREN",
            [
                "all child bboxes are at least "
                f"{PARENT_SIZED_RATIO:.0%} of parent width and height"
            ],
        )

    return _result(
        True,
        "VALID_STRUCTURAL_SPLIT",
        [f"children.length={len(children)}", "all child bboxes valid"],
    )


def validate_expand_instances_result(
    result: Any, parent_size: tuple[int, int]
) -> dict[str, Any]:
    """Return structured repeated-instance effectiveness without semantic VLM review."""

    size = _valid_parent_size(parent_size)
    if size is None:
        return _result(False, "INVALID_PARENT_SIZE", [f"parent_size={parent_size!r}"])
    if not isinstance(result, dict):
        return _result(False, "INVALID_EXPAND_RESULT", ["result is not an object"])

    instance_type = result.get("instance_type")
    if not isinstance(instance_type, str) or not instance_type.strip():
        return _result(
            False, "INVALID_INSTANCE_TYPE", ["instance_type is missing or blank"]
        )
    repeat_count = result.get("repeat_count")
    instances = result.get("instances")
    if not isinstance(instances, list):
        return _result(
            False,
            "INVALID_INSTANCES_COLLECTION",
            ["instances is missing or is not an array"],
        )
    if type(repeat_count) is not int:
        return _result(
            False, "INVALID_REPEAT_COUNT", ["repeat_count is not an integer"]
        )
    if repeat_count < 2 or len(instances) < 2:
        return _result(
            False,
            "INSUFFICIENT_REPEATED_INSTANCES",
            [f"repeat_count={repeat_count}", f"instances.length={len(instances)}"],
        )
    if repeat_count != len(instances):
        return _result(
            False,
            "REPEAT_COUNT_MISMATCH",
            [f"repeat_count={repeat_count}", f"instances.length={len(instances)}"],
        )

    ids = [
        item.get("id") if isinstance(item, dict) else None for item in instances
    ]
    if any(not isinstance(instance_id, str) or not instance_id.strip() for instance_id in ids):
        return _result(
            False, "INVALID_INSTANCE_ID", ["every instance must have a non-empty id"]
        )
    duplicate_ids = sorted(
        instance_id for instance_id, count in Counter(ids).items() if count > 1
    )
    if duplicate_ids:
        return _result(
            False,
            "DUPLICATE_INSTANCE_ID",
            [f"duplicate instance id={instance_id!r}" for instance_id in duplicate_ids],
        )

    bbox_errors, bboxes = _bbox_errors(
        instances, collection="instances", parent_size=size
    )
    if bbox_errors:
        return _result(False, "INVALID_INSTANCE_BBOX", bbox_errors)
    bbox_counts = Counter(bboxes)
    duplicate_member_count = sum(count for count in bbox_counts.values() if count > 1)
    if duplicate_member_count / len(bboxes) >= MASS_DUPLICATE_RATIO:
        return _result(
            False,
            "MASS_DUPLICATE_INSTANCE_BBOX",
            [
                f"duplicate_bbox_members={duplicate_member_count}",
                f"instances.length={len(instances)}",
            ],
        )

    return _result(
        True,
        "VALID_REPEATED_INSTANCES",
        [
            f"repeat_count={repeat_count}",
            f"instances.length={len(instances)}",
            "all instance bboxes valid",
        ],
    )
