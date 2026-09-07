#!/usr/bin/env python3
"""Stage 0 deterministic alpha-hole generator.

This is a pure engineering image-processing step. It never calls a VLM, OCR,
or any image-generation API, and it never performs inpainting or background
repair. It only cuts fully transparent holes into an RGBA copy of the source
image so that a downstream Image-2 repair stage can rebuild the text areas.

Input contract
--------------
``--image``: the original UI screenshot.

``--regions-json``: the FINAL text-region JSON produced after OCR + VLM
secondary localization. Compatible with the existing Stage 0 documents:

- ``vlm-region-plan.json`` written by ``ui_vlm_region_mask_poc.py``: entries
  carry the final ``bbox_source`` rectangle (the VLM-refined box already
  deterministically mapped back to source pixels) plus a ``decision`` field.
  Only entries decided as ``remove_for_background_repair`` are cut; entries
  decided as ``preserve_as_visual_asset`` keep their original pixels.
- ``filtered_texts.json`` written by ``ui_vlm_text_auditor.py``: entries carry
  a source-pixel ``rect`` and every entry is treated as removable.
- ``text-repair-decisions.json`` written by ``ui_text_repair_planner.py``:
  ``decisions`` entries carry a source-pixel ``rect`` and are filtered by
  their ``decision`` value.
- Generic compatibility: a bare JSON list, or a document keyed by ``texts``,
  ``items``, ``decisions``, or ``regions``, whose entries carry ``rect`` /
  ``bbox`` objects or flat ``x`` / ``y`` / ``width`` / ``height`` values.

Coordinate contract
-------------------
Every bbox MUST already be a source-image pixel rectangle. The deterministic
analysis-image -> source-image mapping must happen upstream (in the plan
format that mapped rectangle is ``bbox_source``; ``bbox_analysis`` values are
analysis-image coordinates and are never read here). This script does not
guess resize ratios, does not read ``reported_canvas``, and does not perform
any VLM coordinate inference. If a document or entry explicitly declares a
``coordinate_space`` that is not a source-image pixel space, or if an entry
only provides analysis-image coordinates, the tool fails fast with a clear
error instead of silently producing a wrong hole.

Alpha rules
-----------
Two RGBA outputs are produced:

- alpha-hole.png:
  source RGB is preserved; removal regions have alpha = 0.

- alpha-hole-sanitized.png:
  removal regions are fully cleared to RGBA = (0, 0, 0, 0),
  so no hidden source RGB remains underneath transparent pixels.

alpha-mask.png remains a grayscale debug image. The generated alpha
channel starts fully opaque (255). Every final removal bbox, expanded by
``--padding`` source pixels on all sides and clamped to the image bounds,
becomes fully transparent (alpha = 0). Bboxes that lie completely outside
the image are skipped and reported in the diagnostics. Nothing is filled
black or white, no checkerboard is drawn, and no AI is involved.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


DEFAULT_PADDING = 8
HOLE_OUTPUT_NAME = "alpha-hole.png"
SANITIZED_HOLE_OUTPUT_NAME = "alpha-hole-sanitized.png"
MASK_OUTPUT_NAME = "alpha-mask.png"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

# Document roots that may hold the final region entries, in priority order.
REGION_LIST_KEYS = ("texts", "items", "decisions", "regions")

# A hole is cut only when the final adjudication removes the region. Entries
# preserved as visual assets must keep byte-identical original pixels.
REMOVE_DECISIONS = {"remove_for_background_repair"}
PRESERVE_DECISIONS = {"preserve_as_visual_asset"}
REMOVE_OWNERSHIPS = {"ui_owned"}
PRESERVE_OWNERSHIPS = {"asset_owned"}

# Accepted spellings of "source-image pixel coordinates" for an explicit
# coordinate_space declaration. Anything else fails fast.
SOURCE_COORDINATE_SPACES = {
    "source",
    "source_image",
    "source-image",
    "sourceimage",
    "source_pixel",
    "source_pixels",
    "source-pixels",
    "image",
    "pixel",
    "pixels",
    "px",
}


class AlphaHoleError(RuntimeError):
    """Base error raised by the alpha-hole generator."""


class AlphaHoleInputError(AlphaHoleError):
    """Raised when a local input violates the alpha-hole contract."""


@dataclass(frozen=True)
class RegionBox:
    """One final text rectangle in source-image pixels."""

    x: int
    y: int
    width: int
    height: int


@dataclass
class AlphaHoleDiagnostics:
    """Summary counters for one alpha-hole run."""

    image_width: int
    image_height: int
    padding: int
    region_count: int
    applied_count: int
    skipped_count: int
    preserved_count: int


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AlphaHoleInputError(f"File does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AlphaHoleInputError(f"Cannot read valid JSON from {path}: {exc}") from exc


def _load_bgr_image(path: Path) -> np.ndarray:
    if not path.is_file():
        raise AlphaHoleInputError(f"Source image does not exist: {path}")
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise AlphaHoleInputError(f"Unsupported image extension: {path.suffix}")
    try:
        payload = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(payload, cv2.IMREAD_COLOR)
    except OSError as exc:
        raise AlphaHoleInputError(f"Cannot read image {path}: {exc}") from exc
    if image is None:
        raise AlphaHoleInputError(f"Cannot decode image: {path}")
    return image


def _write_png(path: Path, image: np.ndarray) -> None:
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise AlphaHoleInputError(f"Failed to encode PNG: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded.tofile(path)
    except OSError as exc:
        raise AlphaHoleInputError(f"Failed to write PNG {path}: {exc}") from exc


def _check_coordinate_space(value: Any, location: str) -> None:
    """Fail fast unless an explicit coordinate_space declares source pixels."""

    if value is None:
        return
    if not isinstance(value, str):
        raise AlphaHoleInputError(
            f"{location}: coordinate_space must be a string when present"
        )
    normalized = value.strip().casefold()
    if normalized not in SOURCE_COORDINATE_SPACES:
        raise AlphaHoleInputError(
            f"{location}: coordinate_space={value!r} is not a source-image "
            "pixel space. This tool only accepts bboxes already deterministically "
            "mapped to source-image pixels; rerun the upstream analysis->source "
            "coordinate mapping first."
        )


def _entry_bbox_dict(entry: dict[str, Any], location: str) -> dict[str, Any]:
    """Select the final source-space bbox object from one region entry."""

    for key in ("bbox_source", "rect", "bbox"):
        value = entry.get(key)
        if isinstance(value, dict):
            return value
    if all(key in entry for key in ("x", "y", "width", "height")):
        return entry
    if "bbox_analysis" in entry:
        raise AlphaHoleInputError(
            f"{location}: only analysis-image coordinates (bbox_analysis) are "
            "available. This tool requires final bboxes already mapped to "
            "source-image pixels and refuses to guess the resize ratio."
        )
    raise AlphaHoleInputError(
        f"{location}: missing bbox fields; expected a 'bbox_source', 'rect', "
        "or 'bbox' object or flat 'x', 'y', 'width', 'height' values"
    )


def _region_from_bbox(bbox: dict[str, Any], location: str) -> RegionBox:
    """Validate one bbox object into a RegionBox."""

    missing = [key for key in ("x", "y", "width", "height") if key not in bbox]
    if missing:
        raise AlphaHoleInputError(
            f"{location}: bbox is missing required field(s): {', '.join(missing)}"
        )
    values = {key: bbox[key] for key in ("x", "y", "width", "height")}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise AlphaHoleInputError(
                f"{location}: bbox field {key!r} must be an integer "
                f"source-image pixel value, got {value!r}"
            )
    if values["width"] <= 0 or values["height"] <= 0:
        raise AlphaHoleInputError(
            f"{location}: bbox width/height must be positive, got "
            f"width={values['width']}, height={values['height']}"
        )
    return RegionBox(
        x=values["x"],
        y=values["y"],
        width=values["width"],
        height=values["height"],
    )


def _region_is_preserved(entry: dict[str, Any], location: str) -> bool:
    """Return True when the final adjudication keeps the region as an asset."""

    decision = entry.get("decision")
    if decision is not None:
        if decision in REMOVE_DECISIONS:
            return False
        if decision in PRESERVE_DECISIONS:
            return True
        raise AlphaHoleInputError(f"{location}: unknown decision={decision!r}")
    ownership = entry.get("ownership")
    if ownership is not None:
        if ownership in REMOVE_OWNERSHIPS:
            return False
        if ownership in PRESERVE_OWNERSHIPS:
            return True
        raise AlphaHoleInputError(f"{location}: unknown ownership={ownership!r}")
    return False


def parse_regions(document: Any, path: Path) -> tuple[list[RegionBox], int]:
    """Parse the final regions JSON into source-space boxes.

    Returns the removal boxes plus the number of entries explicitly preserved
    as visual assets.
    """

    root: dict[str, Any] = {}
    if isinstance(document, list):
        raw_entries = document
    elif isinstance(document, dict):
        root = document
        raw_entries = None
        for key in REGION_LIST_KEYS:
            value = document.get(key)
            if isinstance(value, list):
                raw_entries = value
                break
        if raw_entries is None:
            raise AlphaHoleInputError(
                f"{path}: document must be a list or an object containing one "
                f"of the region list keys {list(REGION_LIST_KEYS)}"
            )
    else:
        raise AlphaHoleInputError(f"{path}: document must be a JSON list or object")

    _check_coordinate_space(root.get("coordinate_space"), f"{path} document root")

    regions: list[RegionBox] = []
    preserved_count = 0
    for index, entry in enumerate(raw_entries):
        location = f"{path} entry #{index}"
        if not isinstance(entry, dict):
            raise AlphaHoleInputError(f"{location}: region entry must be a JSON object")
        _check_coordinate_space(entry.get("coordinate_space"), location)
        if _region_is_preserved(entry, location):
            preserved_count += 1
            continue
        bbox = _entry_bbox_dict(entry, location)
        regions.append(_region_from_bbox(bbox, location))
    return regions, preserved_count


def _check_declared_size(
    root: dict[str, Any],
    image_width: int,
    image_height: int,
    path: Path,
) -> None:
    """Fail fast when the document declares a different image size."""

    declared_width: Any = None
    declared_height: Any = None
    source_size = root.get("source_image_size")
    if isinstance(source_size, dict):
        declared_width = source_size.get("width")
        declared_height = source_size.get("height")
    else:
        declared_width = root.get("image_width")
        declared_height = root.get("image_height")
    if declared_width is not None and declared_width != image_width:
        raise AlphaHoleInputError(
            f"{path}: declared image width {declared_width!r} does not match "
            f"the source image width {image_width}"
        )
    if declared_height is not None and declared_height != image_height:
        raise AlphaHoleInputError(
            f"{path}: declared image height {declared_height!r} does not match "
            f"the source image height {image_height}"
        )


def _cut_holes(
    alpha: np.ndarray,
    regions: list[RegionBox],
    padding: int,
) -> tuple[int, int]:
    """Punch every removal region into the alpha channel after clamping.

    Returns the applied and skipped region counts. A region whose raw bbox
    does not intersect the image at all is skipped before padding so that an
    out-of-image box never erases edge pixels via its padding halo.
    """

    image_height, image_width = alpha.shape[:2]
    applied_count = 0
    skipped_count = 0
    for region in regions:
        x0 = region.x
        y0 = region.y
        x1 = region.x + region.width
        y1 = region.y + region.height
        if x0 >= image_width or y0 >= image_height or x1 <= 0 or y1 <= 0:
            skipped_count += 1
            continue
        hole_x0 = max(0, x0 - padding)
        hole_y0 = max(0, y0 - padding)
        hole_x1 = min(image_width, x1 + padding)
        hole_y1 = min(image_height, y1 + padding)
        alpha[hole_y0:hole_y1, hole_x0:hole_x1] = 0
        applied_count += 1
    return applied_count, skipped_count


def generate_alpha_hole(
    image_path: Path | str,
    regions_json_path: Path | str,
    output_dir: Path | str,
    *,
    padding: int = DEFAULT_PADDING,
) -> AlphaHoleDiagnostics:
    """Write alpha-hole.png and alpha-mask.png for one source image.

    The RGB channels of alpha-hole.png are byte-identical to the source image.
    The alpha channel is 255 everywhere except inside the final removal bboxes
    expanded by ``padding`` source pixels, where it is 0. alpha-mask.png is the
    same array written as a grayscale debug image (white = keep, black = hole).
    """

    if padding < 0:
        raise AlphaHoleInputError("padding must be a non-negative integer")
    image_path = Path(image_path)
    regions_path = Path(regions_json_path)
    output_path = Path(output_dir)

    image_bgr = _load_bgr_image(image_path)
    image_height, image_width = image_bgr.shape[:2]

    document = _read_json(regions_path)
    regions, preserved_count = parse_regions(document, regions_path)
    if isinstance(document, dict):
        _check_declared_size(document, image_width, image_height, regions_path)

    alpha = np.full((image_height, image_width), 255, dtype=np.uint8)
    applied_count, skipped_count = _cut_holes(alpha, regions, padding)

    bgra = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = alpha

    # Existing alpha-hole:
    # RGB remains identical to the source, only alpha is set to 0 in hole regions.
    _write_png(output_path / HOLE_OUTPUT_NAME, bgra)

    # Sanitized alpha-hole for Image-2 experiments:
    # Completely clear hidden RGB data inside fully transparent regions.
    sanitized_bgra = bgra.copy()
    hole_pixels = alpha == 0
    sanitized_bgra[hole_pixels] = [0, 0, 0, 0]

    _write_png(
        output_path / SANITIZED_HOLE_OUTPUT_NAME,
        sanitized_bgra,
    )

    _write_png(output_path / MASK_OUTPUT_NAME, alpha)

    return AlphaHoleDiagnostics(
        image_width=image_width,
        image_height=image_height,
        padding=padding,
        region_count=len(regions),
        applied_count=applied_count,
        skipped_count=skipped_count,
        preserved_count=preserved_count,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """Create the Stage 0 alpha-hole command-line parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Stage 0 deterministic alpha-hole generator: cut fully transparent "
            "holes for the final UI text bboxes in an RGBA copy of the source "
            "image. Pure engineering step; no VLM, OCR, or image API calls."
        )
    )
    parser.add_argument(
        "--image",
        type=Path,
        required=True,
        help="source UI screenshot whose RGB content must be preserved",
    )
    parser.add_argument(
        "--regions-json",
        type=Path,
        required=True,
        help=(
            "final text-region JSON (e.g. vlm-region-plan.json with bbox_source, "
            "filtered_texts.json with rect, or text-repair-decisions.json). "
            "Coordinates must ALREADY be source-image pixels: this tool never "
            "rescales analysis-image coordinates, never reads reported_canvas, "
            "and fails fast on a coordinate_space other than a source-image space."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory receiving alpha-hole.png and the alpha-mask.png debug image",
    )
    parser.add_argument(
        "--padding",
        type=int,
        default=DEFAULT_PADDING,
        help=(
            "source-image pixel padding expanded around each final bbox before "
            "cutting the hole, to consume stroke, glow, shadow, and anti-aliasing "
            "halos before Image-2 repair (default: 8)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Stage 0 alpha-hole CLI and return a process exit code."""

    args = build_argument_parser().parse_args(argv)
    try:
        diagnostics = generate_alpha_hole(
            args.image,
            args.regions_json,
            args.output_dir,
            padding=args.padding,
        )
    except (AlphaHoleError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Alpha hole complete: image={diagnostics.image_width}x"
        f"{diagnostics.image_height} region_count={diagnostics.region_count} "
        f"applied={diagnostics.applied_count} skipped={diagnostics.skipped_count} "
        f"preserved={diagnostics.preserved_count} padding={diagnostics.padding}"
    )
    print(f"alpha-hole: {Path(args.output_dir) / HOLE_OUTPUT_NAME}")
    print(
        f"alpha-hole-sanitized: "
        f"{Path(args.output_dir) / SANITIZED_HOLE_OUTPUT_NAME}"
    )
    print(f"alpha-mask: {Path(args.output_dir) / MASK_OUTPUT_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())