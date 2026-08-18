#!/usr/bin/env python3
"""Deterministic Recursive Runtime bbox and child-image utilities."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from build_asset_analysis import map_bbox_to_source
from prepare_analysis_input import DEFAULT_MAX_WIDTH, prepare_analysis_input


def read_image_size(path: Path) -> tuple[int, int]:
    """Read and verify an image size without trusting adapter metadata."""

    if not path.is_file():
        raise ValueError(f"image does not exist or is not a file: {path}")
    try:
        with Image.open(path) as image:
            size = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"unable to read image {path}: {exc}") from exc
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError(f"image has invalid dimensions: {size[0]}x{size[1]}")
    return size


def analysis_bbox_to_crop_bbox(
    bbox: dict[str, int],
    analysis_size: tuple[int, int],
    crop_size: tuple[int, int],
) -> dict[str, int]:
    """Map an Analysis Image bbox into the original Node Crop coordinate space.

    This deliberately reuses the repository's frozen four-edge mapping utility.
    The utility performs the specified independent x/y scaling, rounding, clamp,
    and non-empty validation against the destination image bounds.
    """

    return map_bbox_to_source(bbox, analysis_size, crop_size)


def create_child_node_images(
    *,
    parent_node_crop: Path,
    parent_analysis_image: Path,
    bbox_in_parent_analysis: dict[str, int],
    child_node_crop: Path,
    child_analysis_image: Path,
    child_analysis_metadata: Path,
) -> dict[str, int]:
    """Crop a recursive child from its parent crop and prepare its Analysis Image."""

    crop_size = read_image_size(parent_node_crop)
    analysis_size = read_image_size(parent_analysis_image)
    bbox_in_parent_crop = analysis_bbox_to_crop_bbox(
        bbox_in_parent_analysis,
        analysis_size,
        crop_size,
    )
    x = bbox_in_parent_crop["x"]
    y = bbox_in_parent_crop["y"]
    right = x + bbox_in_parent_crop["width"]
    bottom = y + bbox_in_parent_crop["height"]
    try:
        with Image.open(parent_node_crop) as parent:
            child = parent.crop((x, y, right, bottom))
            child_node_crop.parent.mkdir(parents=True, exist_ok=True)
            child.save(child_node_crop, format="PNG")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(
            f"unable to create child crop from {parent_node_crop}: {exc}"
        ) from exc

    prepare_analysis_input(
        child_node_crop,
        child_analysis_image,
        child_analysis_metadata,
        max_width=DEFAULT_MAX_WIDTH,
        force_width=True,
    )
    return bbox_in_parent_crop

