#!/usr/bin/env python3
"""Deterministic Stage2-B direct crop and foreground extraction.

Backends:
- ``pillow`` (legacy, unchanged): context-ring background + color-distance
  foreground mask with close/open morphology and soft alpha.
- ``sam1_vit_b`` (frozen v0.1): SAM1 ViT-B box-only segmentation with
  max-SAM-score winner selection and deterministic mask postprocess. See
  ``sam_backend.py`` and README "SAM v0.1 baseline".
"""

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

import sam_backend


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

# Only merged into the effective config when backend == "sam1_vit_b", so
# legacy pillow results keep their exact previous config shape.
SAM_DEFAULT_CONFIG: dict[str, Any] = {
    "sam_model_type": sam_backend.SAM_MODEL_TYPE,
    "sam_checkpoint": None,
    "device": "auto",
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
    if config["backend"] == sam_backend.SAM_BACKEND_ID:
        for key, value in SAM_DEFAULT_CONFIG.items():
            config.setdefault(key, value)
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
    sam = foreground and config["backend"] == sam_backend.SAM_BACKEND_ID
    if not foreground:
        mask_method: Any = None
        mask_parameters: dict[str, Any] = {}
    elif sam:
        mask_method = sam_backend.SAM_MASK_METHOD
        mask_parameters = {}
    else:
        mask_method = "color_distance_v0"
        mask_parameters = {
            "close_radius": config["morphology_radius"],
            "open_radius": config["morphology_radius"],
            "foreground_constraint": "final_bbox_core",
        }
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
        "mask_method": mask_method,
        "mask_threshold": None if (sam or not foreground) else config["mask_threshold"],
        "mask_parameters": mask_parameters,
        "alpha_parameters": (
            {
                "dilation_radius": 0,
                "gaussian_blur_radius": 0.0,
                "source_alpha_rule": "multiply",
                "alpha_representation": "straight",
            }
            if sam
            else (
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
            )
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


def _extract_one_sam(
    source_rgba: np.ndarray,
    source_image: str,
    asset: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Frozen v0.1 SAM path: box-only prompt over the encoded full source.

    ``context`` carries the shared predictor (encoded once per request) plus
    its load metadata. The bbox is the reviewed ``final_bbox`` in
    source-image pixel coordinates.
    """

    image_height, image_width = source_rgba.shape[:2]
    image_size = (image_width, image_height)
    final_bbox = deepcopy(asset["final_bbox"])
    if final_bbox["width"] <= 0 or final_bbox["height"] <= 0:
        raise ExtractionError("final_bbox width/height must be > 0")
    if not bbox_is_in_bounds(final_bbox, image_size):
        raise ExtractionError("final_bbox is outside source-image bounds")

    record = _base_record(asset, source_image, config)
    output_relative = Path("assets") / f"{asset['asset_id']}.png"
    output_path = output_dir / output_relative

    predictor = context["predictor"]
    box_xyxy = bbox_edges(final_bbox)
    masks, scores = sam_backend.predict_box(predictor, box_xyxy)
    candidate_count = int(masks.shape[0])
    if candidate_count == 0:
        raise ExtractionError("SAM returned no candidates for the bbox prompt", record)

    winner_index = sam_backend.select_winner(scores)
    winner_mask = masks[winner_index]
    filtered_mask, post_stats = sam_backend.postprocess_sam_mask(winner_mask)
    if not filtered_mask.any():
        raise ExtractionError("SAM winner mask is empty after postprocess", record)

    roi, offset = build_extraction_roi(final_bbox, image_size, config["roi_padding"])
    roi_x1, roi_y1, roi_x2, roi_y2 = bbox_edges(roi)
    roi_rgba = source_rgba[roi_y1:roi_y2, roi_x1:roi_x2].copy()
    mask_roi = filtered_mask[roi_y1:roi_y2, roi_x1:roi_x2]
    if roi_rgba.size == 0 or mask_roi.size == 0:
        raise ExtractionError("extraction ROI is empty", record)

    rgba = compose_rgba(roi_rgba, mask_roi.astype(np.uint8) * 255)
    if not rgba[:, :, 3].any():
        raise ExtractionError("composed alpha is empty", record)

    sam_info = context["info"]
    record.update(
        {
            "extraction_roi": roi,
            "final_bbox_offset": offset,
            "mask_parameters": {
                "model": sam_info["model"],
                "model_type": sam_info["model_type"],
                "prompt": sam_backend.PROMPT,
                "device_requested": sam_info["requested_device"],
                "device": sam_info["device"],
                "device_fallback": sam_info["device_fallback"],
                "candidate_count": candidate_count,
                "candidates": sam_backend.build_candidates_metadata(masks, scores),
                "winner_index": winner_index,
                "winner_sam_score": float(scores[winner_index]),
                "winner_selection": "max_sam_score",
                "postprocess": {
                    "close_kernel": sam_backend.CLOSE_KERNEL,
                    "close_iterations": sam_backend.CLOSE_ITERATIONS,
                    "connectivity": sam_backend.CONNECTIVITY,
                    "relative_component_threshold": sam_backend.RELATIVE_COMPONENT_THRESHOLD,
                },
                "mask_area": post_stats["pixels"]["after_filter"],
                "component_count": post_stats["connected_components"]["component_count"],
                "kept_component_count": post_stats["connected_components"]["kept_component_count"],
                "removed_component_count": post_stats["connected_components"]["removed_component_count"],
                "removed_components": post_stats["connected_components"]["removed_components"],
            },
        }
    )

    mask_relative = Path("masks") / f"{asset['asset_id']}_mask.png"
    mask_path = output_dir / mask_relative
    _save_png(mask_roi.astype(np.uint8) * 255, mask_path, "L")
    _save_png(rgba, output_path, "RGBA")
    record.update(
        {
            "status": "success",
            "output_path": output_relative.as_posix(),
            "mask_path": mask_relative.as_posix(),
        }
    )
    return record


def extract_one(
    source_rgba: np.ndarray,
    source_image: str,
    asset: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    sam_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    image_height, image_width = source_rgba.shape[:2]
    image_size = (image_width, image_height)
    final_bbox = deepcopy(asset["final_bbox"])
    if final_bbox["width"] <= 0 or final_bbox["height"] <= 0:
        raise ExtractionError("final_bbox width/height must be > 0")
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

    if config["backend"] == sam_backend.SAM_BACKEND_ID:
        if sam_context is None:
            raise RuntimeError(
                "sam1_vit_b backend requires an encoded SAM context; call "
                "execute_request or prepare it via sam_backend.load_sam_predictor"
            )
        return _extract_one_sam(
            source_rgba,
            source_image,
            asset,
            config,
            output_dir,
            sam_context,
        )

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

    sam_context: dict[str, Any] | None = None
    if load_failure is None and config["backend"] == sam_backend.SAM_BACKEND_ID:
        # Load the predictor and encode the full source image exactly once.
        # Per-asset failures never fall back to another segmentation method.
        predictor, sam_info = sam_backend.load_sam_predictor(
            config["sam_model_type"],
            config["sam_checkpoint"],
            config["device"],
        )
        sam_backend.encode_source(predictor, source_rgba)
        sam_context = {"predictor": predictor, "info": sam_info}

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
                    sam_context=sam_context,
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
        "backend": config["backend"],
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
    parser.add_argument(
        "--backend",
        choices=[BACKEND, sam_backend.SAM_BACKEND_ID],
        default=None,
        help="Extraction backend; overrides config.backend",
    )
    parser.add_argument(
        "--sam-checkpoint",
        default=None,
        help="Path to the SAM ViT-B checkpoint (required for the sam1_vit_b backend; never auto-downloaded)",
    )
    parser.add_argument(
        "--sam-model-type",
        choices=[sam_backend.SAM_MODEL_TYPE],
        default=None,
        help="SAM model type; frozen v0.1 supports vit_b only",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default=None,
        help="Torch device for SAM (auto falls back to CPU when CUDA is unavailable)",
    )
    return parser.parse_args(argv)


def apply_cli_overrides(request: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Apply CLI backend options on top of the request config (CLI wins)."""

    overrides = {
        "backend": args.backend,
        "sam_checkpoint": args.sam_checkpoint,
        "sam_model_type": args.sam_model_type,
        "device": args.device,
    }
    config = request.setdefault("config", {})
    for key, value in overrides.items():
        if value is not None:
            config[key] = value
    return request


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    request_path = Path(args.request)
    try:
        request = load_json(request_path)
        apply_cli_overrides(request, args)
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
