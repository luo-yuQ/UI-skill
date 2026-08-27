"""Standalone deterministic PoC for repairing text over smooth UI surfaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


MODELS = {
    "constant": 1,
    "linear": 3,
    "quadratic": 6,
}


def _read_image(path: Path, flags: int) -> np.ndarray:
    encoded = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(encoded, flags)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return image


def _write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise ValueError(f"Could not encode image: {path}")
    encoded.tofile(str(path))


def load_text_items(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", data if isinstance(data, list) else [])
    return {item["id"]: item for item in items}


def compute_roi(rect: dict[str, int], image_shape: tuple[int, ...], padding: int | None = None) -> tuple[int, int, int, int]:
    height, width = image_shape[:2]
    pad = max(8, int(round(rect["height"] * 1.25))) if padding is None else max(0, padding)
    x0 = max(0, rect["x"] - pad)
    y0 = max(0, rect["y"] - pad)
    x1 = min(width, rect["x"] + rect["width"] + pad)
    y1 = min(height, rect["y"] + rect["height"] + pad)
    return x0, y0, x1 - x0, y1 - y0


def split_samples(candidate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return deterministic, spatially dispersed fit and validation coordinates."""
    ys, xs = np.where(candidate)
    validation = ((xs + 2 * ys) % 5) == 0
    return np.column_stack((ys[~validation], xs[~validation])), np.column_stack((ys[validation], xs[validation]))


def _design_matrix(xs: np.ndarray, ys: np.ndarray, model: str, width: int, height: int) -> np.ndarray:
    x = (xs.astype(np.float64) / max(width - 1, 1)) * 2.0 - 1.0
    y = (ys.astype(np.float64) / max(height - 1, 1)) * 2.0 - 1.0
    if model == "constant":
        return np.column_stack((np.ones_like(x),))
    if model == "linear":
        return np.column_stack((np.ones_like(x), x, y))
    return np.column_stack((np.ones_like(x), x, y, x * x, x * y, y * y))


def fit_surface(image: np.ndarray, fit_coords: np.ndarray, validation_coords: np.ndarray, model: str) -> dict[str, Any]:
    """Fit one surface and report MAE on disjoint fit/validation samples."""
    if model not in MODELS:
        raise ValueError(f"Unknown model: {model}")
    required = MODELS[model]
    minimum_fit_samples = max(20, required * 3)
    if len(fit_coords) < minimum_fit_samples or len(validation_coords) < 5:
        return {"status": "insufficient_samples"}
    ys, xs = fit_coords[:, 0], fit_coords[:, 1]
    design = _design_matrix(xs, ys, model, image.shape[1], image.shape[0])
    if model == "constant":
        coefficients = [np.array([np.median(image[ys, xs, channel])]) for channel in range(image.shape[2])]
    else:
        condition = float(np.linalg.cond(design))
        if not np.isfinite(condition) or condition > 1e10:
            return {"status": "failed", "reason": "ill_conditioned_design"}
        coefficients = []
        try:
            for channel in range(image.shape[2]):
                solution, _, rank, _ = np.linalg.lstsq(design, image[ys, xs, channel].astype(np.float64), rcond=None)
                if rank < required or not np.all(np.isfinite(solution)):
                    return {"status": "failed", "reason": "rank_or_non_finite_solution"}
                coefficients.append(solution)
        except np.linalg.LinAlgError:
            return {"status": "failed", "reason": "least_squares_error"}

    def error(coords: np.ndarray) -> float:
        pred = np.column_stack([_design_matrix(coords[:, 1], coords[:, 0], model, image.shape[1], image.shape[0]) @ c for c in coefficients])
        actual = image[coords[:, 0], coords[:, 1]].astype(np.float64)
        return float(np.mean(np.abs(pred - actual)))

    return {
        "status": "ok",
        "fit_mae": error(fit_coords),
        "validation_mae": error(validation_coords),
        "sample_count": int(len(fit_coords) + len(validation_coords)),
        "fit_sample_count": int(len(fit_coords)),
        "validation_sample_count": int(len(validation_coords)),
        "coefficients": [c.tolist() for c in coefficients],
    }


def _surface_image(image: np.ndarray, mask: np.ndarray, fit_coords: np.ndarray, validation_coords: np.ndarray, model: str) -> tuple[np.ndarray, dict[str, Any]]:
    result = fit_surface(image, fit_coords, validation_coords, model)
    output = image.copy()
    if result.get("status") != "ok":
        return output, result
    coefficients = [np.asarray(c) for c in result["coefficients"]]
    ys, xs = np.indices(mask.shape)
    design = _design_matrix(xs.ravel(), ys.ravel(), model, image.shape[1], image.shape[0])
    prediction = np.column_stack([design @ c for c in coefficients]).reshape(image.shape)
    output[mask] = np.clip(np.rint(prediction[mask]), 0, 255).astype(np.uint8)
    if not np.array_equal(output[~mask], image[~mask]):
        raise AssertionError("surface repair modified pixels outside the text mask")
    return output, result


def _sample_map(mask: np.ndarray, excluded: np.ndarray, fit: np.ndarray, validation: np.ndarray) -> np.ndarray:
    debug = np.zeros((*mask.shape, 3), dtype=np.uint8)
    debug[~excluded] = (45, 45, 45)
    debug[excluded] = (80, 80, 80)
    debug[mask] = (0, 0, 255)
    debug[fit[:, 0], fit[:, 1]] = (0, 200, 0)
    debug[validation[:, 0], validation[:, 1]] = (0, 220, 255)
    return debug


def _comparison(images: list[tuple[str, np.ndarray]]) -> np.ndarray:
    panels = []
    for label, image in images:
        panel = image.copy()
        cv2.rectangle(panel, (0, 0), (panel.shape[1] - 1, 25), (25, 25, 25), -1)
        cv2.putText(panel, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(panel)
    return np.hstack(panels)


def process_item(image: np.ndarray, full_mask: np.ndarray, item: dict[str, Any], output_dir: Path, padding: int | None, guard_band: int | None) -> dict[str, Any]:
    x, y, width, height = compute_roi(item["rect"], image.shape, padding)
    crop = image[y:y + height, x:x + width].copy()
    mask = full_mask[y:y + height, x:x + width] > 0
    band = max(2, int(round(item["rect"]["height"] * 0.2))) if guard_band is None else max(0, guard_band)
    excluded = cv2.dilate(mask.astype(np.uint8), np.ones((2 * band + 1, 2 * band + 1), np.uint8), iterations=1).astype(bool)
    fit, validation = split_samples(~excluded)
    folder = output_dir / item["id"]
    folder.mkdir(parents=True, exist_ok=True)
    _write_image(folder / "original_crop.png", crop)
    _write_image(folder / "mask_crop.png", (mask.astype(np.uint8) * 255))
    _write_image(folder / "sample_map.png", _sample_map(mask, excluded, fit, validation))
    telea = cv2.inpaint(crop, mask.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
    _write_image(folder / "telea.png", telea)
    models = {}
    outputs = [("Original", crop), ("Telea", telea)]
    for model in MODELS:
        repaired, metrics = _surface_image(crop, mask, fit, validation, model)
        models[model] = {key: value for key, value in metrics.items() if key != "coefficients"}
        _write_image(folder / f"{model}.png", repaired)
        outputs.append((model.title(), repaired))
    _write_image(folder / "comparison.png", _comparison(outputs))
    return {"text_id": item["id"], "text": item.get("text", ""), "roi": {"x": x, "y": y, "width": width, "height": height}, "guard_band": band, "samples": {"total": int(len(fit) + len(validation)), "fit": int(len(fit)), "validation": int(len(validation))}, "models": models, "lowest_validation_error_model": min((name for name, data in models.items() if data.get("status") == "ok"), key=lambda name: models[name]["validation_mae"], default=None)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--texts-json", type=Path, required=True)
    parser.add_argument("--text-ids", required=True, help="Comma-separated text IDs")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--roi-padding", type=int)
    parser.add_argument("--guard-band", type=int)
    args = parser.parse_args()
    image = _read_image(args.image, cv2.IMREAD_COLOR)
    full_mask = _read_image(args.mask, cv2.IMREAD_GRAYSCALE)
    if image.shape[:2] != full_mask.shape[:2]:
        raise ValueError("image and mask dimensions must match")
    items = load_text_items(args.texts_json)
    requested_ids = [value.strip() for value in args.text_ids.split(",") if value.strip()]
    missing = [text_id for text_id in requested_ids if text_id not in items]
    if missing:
        raise ValueError(f"Unknown text IDs: {', '.join(missing)}")
    selected = [items[text_id] for text_id in requested_ids]
    metrics = {"schema_version": "0.1", "items": [process_item(image, full_mask, item, args.output_dir, args.roi_padding, args.guard_band) for item in selected]}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
