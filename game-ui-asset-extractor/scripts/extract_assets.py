#!/usr/bin/env python3
"""Deterministic Stage2-B direct crop and foreground extraction."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image, ImageFilter, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
REQUEST_SCHEMA_PATH = ROOT / "schemas" / "extraction-request.schema.json"
RESULT_SCHEMA_PATH = ROOT / "schemas" / "extraction-result.schema.json"
SCHEMA_VERSION = "0.1"
BACKEND = "pillow"

DEFAULT_CONFIG: dict[str, Any] = {
    "backend": BACKEND,
    "roi_padding": 10,
    "background_min_pixels": 16,
    "mask_threshold": 22.0,
    "morphology_radius": 1,
    "alpha_dilation_radius": 1,
    "alpha_blur_radius": 1.0,
}


class ExtractionError(RuntimeError):
    """An explicit per-asset extraction failure."""

    def __init__(self, message: str, record: dict[str, Any] | None = None):
        super().__init__(message)
        self.record = record


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validation_errors(document: Any, schema_path: Path) -> list[str]:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    return [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in errors
    ]


def validate_request(request: Any) -> list[str]:
    errors = validation_errors(request, REQUEST_SCHEMA_PATH)
    if isinstance(request, dict) and isinstance(request.get("assets"), list):
        ids = [asset.get("asset_id") for asset in request["assets"] if isinstance(asset, dict)]
        duplicates = sorted({asset_id for asset_id in ids if ids.count(asset_id) > 1})
        if duplicates:
            errors.append(f"assets: duplicate asset_id values: {', '.join(duplicates)}")
    return errors


def validate_result(result: Any) -> list[str]:
    return validation_errors(result, RESULT_SCHEMA_PATH)


def effective_config(request: dict[str, Any]) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    config.update(request.get("config", {}))
    return config


def bbox_edges(bbox: dict[str, int]) -> tuple[int, int, int, int]:
    return (
        bbox["x"],
        bbox["y"],
        bbox["x"] + bbox["width"],
        bbox["y"] + bbox["height"],
    )


def bbox_is_in_bounds(
    bbox: dict[str, int], image_size: tuple[int, int]
) -> bool:
    image_width, image_height = image_size
    x1, y1, x2, y2 = bbox_edges(bbox)
    return 0 <= x1 < x2 <= image_width and 0 <= y1 < y2 <= image_height


def build_extraction_roi(
    final_bbox: dict[str, int],
    image_size: tuple[int, int],
    padding: int,
) -> tuple[dict[str, int], dict[str, int]]:
    """Expand final_bbox by fixed pixels and clamp only the derived ROI."""

    if padding < 0:
        raise ValueError("padding must be >= 0")
    image_width, image_height = image_size
    x1, y1, x2, y2 = bbox_edges(final_bbox)
    roi_x1 = max(0, x1 - padding)
    roi_y1 = max(0, y1 - padding)
    roi_x2 = min(image_width, x2 + padding)
    roi_y2 = min(image_height, y2 + padding)
    if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
        raise ExtractionError("extraction ROI is empty")
    roi = {
        "x": roi_x1,
        "y": roi_y1,
        "width": roi_x2 - roi_x1,
        "height": roi_y2 - roi_y1,
    }
    offset = {"x": x1 - roi_x1, "y": y1 - roi_y1}
    return roi, offset


def _region_mask(
    shape: tuple[int, int],
    final_bbox: dict[str, int],
    offset: dict[str, int],
) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    x1 = offset["x"]
    y1 = offset["y"]
    x2 = x1 + final_bbox["width"]
    y2 = y1 + final_bbox["height"]
    mask[y1:y2, x1:x2] = True
    return mask


def context_ring_mask(
    roi_shape: tuple[int, int],
    final_bbox: dict[str, int],
    offset: dict[str, int],
) -> np.ndarray:
    """Return ROI pixels outside the immutable final-bbox core."""

    return ~_region_mask(roi_shape, final_bbox, offset)


def _border_mask(shape: tuple[int, int], thickness: int = 1) -> np.ndarray:
    height, width = shape
    thickness = max(1, min(thickness, height, width))
    mask = np.zeros((height, width), dtype=bool)
    mask[:thickness, :] = True
    mask[-thickness:, :] = True
    mask[:, :thickness] = True
    mask[:, -thickness:] = True
    return mask


def _opaque_pixels(rgba: np.ndarray, sample_mask: np.ndarray) -> np.ndarray:
    valid = sample_mask & (rgba[:, :, 3] > 0)
    return rgba[:, :, :3][valid]


def estimate_local_background(
    roi_rgba: np.ndarray,
    source_rgba: np.ndarray,
    final_bbox: dict[str, int],
    offset: dict[str, int],
    min_pixels: int,
) -> tuple[np.ndarray, str, dict[str, int]]:
    """Estimate background from Context Ring with explicit fallbacks."""

    ring_pixels = _opaque_pixels(
        roi_rgba,
        context_ring_mask(roi_rgba.shape[:2], final_bbox, offset),
    )
    roi_border_pixels = _opaque_pixels(
        roi_rgba,
        _border_mask(roi_rgba.shape[:2]),
    )
    global_border_pixels = _opaque_pixels(
        source_rgba,
        _border_mask(source_rgba.shape[:2]),
    )

    if len(ring_pixels) >= min_pixels:
        samples = ring_pixels
        method = "context_ring_median"
    elif len(roi_border_pixels) >= min_pixels:
        samples = roi_border_pixels
        method = "roi_border_median_fallback"
    elif len(global_border_pixels) > 0:
        samples = global_border_pixels
        method = "global_border_median_fallback"
    else:
        raise ExtractionError("no opaque pixels available for background estimation")

    background = np.median(samples, axis=0).astype(np.uint8)
    parameters = {
        "minimum_sample_pixels": int(min_pixels),
        "context_ring_pixel_count": int(len(ring_pixels)),
        "roi_border_pixel_count": int(len(roi_border_pixels)),
        "global_border_pixel_count": int(len(global_border_pixels)),
        "used_sample_pixel_count": int(len(samples)),
    }
    return background, method, parameters


def color_distance_mask(
    roi_rgba: np.ndarray,
    background_rgb: np.ndarray,
    threshold: float,
    final_bbox: dict[str, int],
    offset: dict[str, int],
) -> np.ndarray:
    pixels = roi_rgba[:, :, :3].astype(np.float32)
    background = background_rgb.astype(np.float32)
    distance = np.linalg.norm(pixels - background, axis=2)
    core = _region_mask(roi_rgba.shape[:2], final_bbox, offset)
    return (distance > threshold) & core


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius == 0:
        return mask.copy()
    image = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    filtered = image.filter(ImageFilter.MaxFilter(radius * 2 + 1))
    return np.asarray(filtered) > 127


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius == 0:
        return mask.copy()
    image = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    filtered = image.filter(ImageFilter.MinFilter(radius * 2 + 1))
    return np.asarray(filtered) > 127


def postprocess_binary_mask(
    mask: np.ndarray,
    radius: int,
    final_bbox: dict[str, int],
    offset: dict[str, int],
) -> np.ndarray:
    """Apply close then open, retaining final_bbox as the foreground core."""

    if radius < 0:
        raise ValueError("morphology radius must be >= 0")
    closed = _erode(_dilate(mask, radius), radius)
    opened = _dilate(_erode(closed, radius), radius)
    core = _region_mask(mask.shape, final_bbox, offset)
    return opened & core


def soft_alpha(mask: np.ndarray, dilation_radius: int, blur_radius: float) -> np.ndarray:
    alpha = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    if dilation_radius > 0:
        alpha = alpha.filter(ImageFilter.MaxFilter(dilation_radius * 2 + 1))
    if blur_radius > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    return np.asarray(alpha, dtype=np.uint8)


def compose_rgba(roi_rgba: np.ndarray, generated_alpha: np.ndarray) -> np.ndarray:
    """Preserve source RGB and multiply source alpha by generated alpha."""

    result = roi_rgba.copy()
    source_alpha = result[:, :, 3].astype(np.uint16)
    mask_alpha = generated_alpha.astype(np.uint16)
    result[:, :, 3] = ((source_alpha * mask_alpha + 127) // 255).astype(np.uint8)
    return result


def _save_png(array: np.ndarray, path: Path, mode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        Image.fromarray(array, mode=mode).save(temporary, format="PNG", optimize=True)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _base_record(
    asset: dict[str, Any],
    source_image: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    foreground = asset["extraction_mode"] == "foreground_extract"
    return {
        "asset_id": asset["asset_id"],
        "asset_type": asset["asset_type"],
        "extraction_mode": asset["extraction_mode"],
        "status": "failed",
        "source_image": source_image,
        "final_bbox": deepcopy(asset["final_bbox"]),
        "extraction_roi": None,
        "roi_padding": config["roi_padding"] if foreground else 0,
        "final_bbox_offset": None,
        "background_method": None,
        "background_rgb": None,
        "background_parameters": {},
        "mask_method": "color_distance_v0" if foreground else None,
        "mask_threshold": config["mask_threshold"] if foreground else None,
        "mask_parameters": (
            {
                "close_radius": config["morphology_radius"],
                "open_radius": config["morphology_radius"],
                "foreground_constraint": "final_bbox_core",
            }
            if foreground
            else {}
        ),
        "alpha_parameters": (
            {
                "dilation_radius": config["alpha_dilation_radius"],
                "gaussian_blur_radius": config["alpha_blur_radius"],
                "source_alpha_rule": "multiply",
                "alpha_representation": "straight",
            }
            if foreground
            else {
                "dilation_radius": 0,
                "gaussian_blur_radius": 0.0,
                "source_alpha_rule": "preserve",
                "alpha_representation": "straight",
            }
        ),
        "output_path": None,
        "mask_path": None,
    }


def _failure_record(
    asset: dict[str, Any],
    source_image: str,
    config: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    record = _base_record(asset, source_image, config)
    record["failure_reason"] = reason
    return record


def extract_one(
    source_rgba: np.ndarray,
    source_image: str,
    asset: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    image_height, image_width = source_rgba.shape[:2]
    image_size = (image_width, image_height)
    final_bbox = deepcopy(asset["final_bbox"])
    if not bbox_is_in_bounds(final_bbox, image_size):
        raise ExtractionError("final_bbox is outside source-image bounds")

    record = _base_record(asset, source_image, config)
    output_relative = Path("assets") / f"{asset['asset_id']}.png"
    output_path = output_dir / output_relative

    if asset["extraction_mode"] == "direct_crop":
        x1, y1, x2, y2 = bbox_edges(final_bbox)
        crop = source_rgba[y1:y2, x1:x2].copy()
        if crop.size == 0:
            raise ExtractionError("direct crop is empty")
        _save_png(crop, output_path, "RGBA")
        record.update(
            {
                "status": "success",
                "extraction_roi": deepcopy(final_bbox),
                "final_bbox_offset": {"x": 0, "y": 0},
                "output_path": output_relative.as_posix(),
            }
        )
        return record

    roi, offset = build_extraction_roi(
        final_bbox,
        image_size,
        config["roi_padding"],
    )
    roi_x1, roi_y1, roi_x2, roi_y2 = bbox_edges(roi)
    roi_rgba = source_rgba[roi_y1:roi_y2, roi_x1:roi_x2].copy()
    if roi_rgba.size == 0:
        raise ExtractionError("extraction ROI is empty")
    record.update(
        {
            "extraction_roi": roi,
            "final_bbox_offset": offset,
        }
    )

    background, method, background_parameters = estimate_local_background(
        roi_rgba,
        source_rgba,
        final_bbox,
        offset,
        config["background_min_pixels"],
    )
    record.update(
        {
            "background_method": method,
            "background_rgb": [int(channel) for channel in background],
            "background_parameters": background_parameters,
        }
    )
    initial_mask = color_distance_mask(
        roi_rgba,
        background,
        config["mask_threshold"],
        final_bbox,
        offset,
    )
    binary_mask = postprocess_binary_mask(
        initial_mask,
        config["morphology_radius"],
        final_bbox,
        offset,
    )
    if not binary_mask.any():
        raise ExtractionError("foreground mask is empty", record)

    alpha = soft_alpha(
        binary_mask,
        config["alpha_dilation_radius"],
        config["alpha_blur_radius"],
    )
    rgba = compose_rgba(roi_rgba, alpha)
    if not rgba[:, :, 3].any():
        raise ExtractionError("composed alpha is empty", record)

    mask_relative = Path("masks") / f"{asset['asset_id']}_mask.png"
    mask_path = output_dir / mask_relative
    _save_png(binary_mask.astype(np.uint8) * 255, mask_path, "L")
    _save_png(rgba, output_path, "RGBA")
    record.update(
        {
            "status": "success",
            "output_path": output_relative.as_posix(),
            "mask_path": mask_relative.as_posix(),
        }
    )
    return record


def execute_request(
    request: dict[str, Any],
    output_dir: Path,
    *,
    source_base: Path | None = None,
) -> dict[str, Any]:
    errors = validate_request(request)
    if errors:
        raise ValueError("Invalid extraction request:\n" + "\n".join(errors))

    output_dir.mkdir(parents=True, exist_ok=True)
    config = effective_config(request)
    source_text = request["source_image"]
    source_path = Path(source_text)
    if not source_path.is_absolute():
        source_path = (source_base or Path.cwd()) / source_path

    source_rgba: np.ndarray | None = None
    source_size: dict[str, int] | None = None
    load_failure: str | None = None
    try:
        if not source_path.is_file():
            raise FileNotFoundError(f"source image not found: {source_path}")
        with Image.open(source_path) as image:
            rgba_image = image.convert("RGBA")
            source_size = {"width": rgba_image.width, "height": rgba_image.height}
            source_rgba = np.asarray(rgba_image, dtype=np.uint8).copy()
    except (FileNotFoundError, OSError, UnidentifiedImageError) as exc:
        load_failure = str(exc)

    results: list[dict[str, Any]] = []
    for asset in request["assets"]:
        if load_failure is not None or source_rgba is None:
            results.append(_failure_record(asset, source_text, config, load_failure or "source image load failed"))
            continue
        try:
            results.append(
                extract_one(
                    source_rgba,
                    source_text,
                    asset,
                    config,
                    output_dir,
                )
            )
        except ExtractionError as exc:
            if exc.record is None:
                results.append(_failure_record(asset, source_text, config, str(exc)))
            else:
                failed_record = exc.record
                failed_record["status"] = "failed"
                failed_record["output_path"] = None
                failed_record["mask_path"] = None
                failed_record["failure_reason"] = str(exc)
                results.append(failed_record)
        except (OSError, ValueError) as exc:
            results.append(_failure_record(asset, source_text, config, str(exc)))

    document = {
        "schema_version": SCHEMA_VERSION,
        "status": "success" if all(result["status"] == "success" for result in results) else "failed",
        "source_image": source_text,
        "source_size": source_size,
        "backend": BACKEND,
        "config": config,
        "assets": results,
    }
    result_errors = validate_result(document)
    if result_errors:
        raise RuntimeError("Internal extraction result is invalid:\n" + "\n".join(result_errors))

    result_path = output_dir / "extraction-result.json"
    result_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return document


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Stage2-B PNG assets from immutable final bboxes."
    )
    parser.add_argument("--request", required=True, help="Path to extraction request JSON")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    request_path = Path(args.request)
    try:
        request = load_json(request_path)
        result = execute_request(
            request,
            Path(args.output_dir),
            source_base=request_path.resolve().parent,
        )
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
