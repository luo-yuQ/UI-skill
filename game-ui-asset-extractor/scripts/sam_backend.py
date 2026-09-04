"""SAM1 ViT-B box-only backend for Stage2-B asset extraction (frozen v0.1).

Frozen baseline contract (see README "SAM v0.1 baseline"):

- Model: SAM1 ViT-B via the ``segment-anything`` package. Checkpoints are
  never auto-downloaded and never resolved from hardcoded paths.
- Authoritative input: the full source image plus a reviewed bbox in
  source-image pixel coordinates. Tight crops are never encoded.
- The full source image is encoded exactly once per request
  (``predictor.set_image``); every asset is prompted with a box only.
- Prompt: box only (``predictor.predict(box=..., multimask_output=True)``).
  Point prompting is NOT part of v0.1 and is reserved for future fallback.
- Candidate selection: winner = max SAM score. Geometry-aware scoring was
  evaluated offline and is intentionally not adopted.
- Mask postprocess: deterministic 3x3 binary close (dilate 1px, erode 1px)
  followed by 8-connected component filtering that keeps components with
  area >= 8% of the largest component (plus any positive-point hits, which
  are always empty in v0.1 because there are no points).
- RGBA output: source RGB is preserved; alpha = postprocessed mask * source
  alpha. No matting, halo repair, or occlusion completion (Stage2-C scope).

This module must stay importable without torch / segment-anything so that
the Pillow backend and the unit-test suite never require them.
"""

from __future__ import annotations

from typing import Any

import numpy as np

SAM_BACKEND_ID = "sam1_vit_b"
SAM_MASK_METHOD = "sam1_box_v0"
SAM_MODEL_TYPE = "vit_b"

CLOSE_KERNEL = "3x3_ones"
CLOSE_ITERATIONS = 1
CONNECTIVITY = 8
RELATIVE_COMPONENT_THRESHOLD = 0.08

PROMPT = "box_only"


class SamBackendError(RuntimeError):
    """An explicit, diagnosable SAM backend failure."""


def resolve_device(device: str) -> tuple[str, bool]:
    """Resolve the requested device, falling back to CPU when CUDA is absent.

    Returns ``(effective_device, fell_back)``. Only ``auto`` / ``cuda`` /
    ``cpu`` are accepted; anything else is an explicit configuration error.
    """

    if device == "cpu":
        return "cpu", False
    if device == "cuda":
        return _resolve_cuda(fallback=False)
    if device == "auto":
        return _resolve_cuda(fallback=True)
    raise SamBackendError(
        f"invalid SAM device '{device}': expected one of 'auto', 'cuda', 'cpu'"
    )


def _resolve_cuda(*, fallback: bool) -> tuple[str, bool]:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised only without torch
        raise SamBackendError(
            "torch is required for device detection; install torch and "
            "segment-anything to use the sam1_vit_b backend"
        ) from exc
    cuda_available = bool(torch.cuda.is_available())
    if cuda_available:
        return "cuda", False
    if fallback:
        return "cpu", True
    raise SamBackendError("device 'cuda' was requested but CUDA is not available")


def load_sam_predictor(
    model_type: str,
    checkpoint: str,
    device: str,
) -> tuple[Any, dict[str, Any]]:
    """Build a SamPredictor for the explicitly configured checkpoint.

    Returns ``(predictor, info)`` where ``info`` records the effective model
    type, device, and any device fallback for result metadata. Never
    auto-downloads checkpoints and never mutates the filesystem.
    """

    if model_type != SAM_MODEL_TYPE:
        raise SamBackendError(
            f"unsupported SAM model type '{model_type}': frozen v0.1 baseline "
            f"only supports '{SAM_MODEL_TYPE}'"
        )
    if not checkpoint:
        raise SamBackendError(
            "SAM checkpoint path is required: pass --sam-checkpoint or set "
            "config.sam_checkpoint"
        )
    checkpoint_path = _as_path(checkpoint)
    if not checkpoint_path.is_file():
        raise SamBackendError(f"SAM checkpoint not found: {checkpoint_path}")

    try:
        import torch  # noqa: F401
        from segment_anything import SamPredictor, sam_model_registry
    except ImportError as exc:
        raise SamBackendError(
            "segment-anything (and torch) are not installed; install them to "
            "use the sam1_vit_b backend"
        ) from exc

    effective_device, fell_back = resolve_device(device)
    try:
        sam = sam_model_registry[model_type](checkpoint=str(checkpoint_path))
        sam.to(device=effective_device)
        predictor = SamPredictor(sam)
    except Exception as exc:
        raise SamBackendError(
            f"failed to load SAM checkpoint {checkpoint_path}: {exc}"
        ) from exc
    info = {
        "model": SAM_BACKEND_ID,
        "model_type": model_type,
        "checkpoint": checkpoint_path.as_posix(),
        "requested_device": device,
        "device": effective_device,
        "device_fallback": fell_back,
    }
    return predictor, info


def _as_path(checkpoint: str):
    from pathlib import Path

    return Path(checkpoint).expanduser()


def encode_source(predictor: Any, source_rgb: np.ndarray) -> None:
    """Encode the full source image exactly once (performance contract)."""

    if source_rgb.ndim != 3 or source_rgb.shape[2] < 3:
        raise SamBackendError("source image must be an HxWx3+ array for SAM encoding")
    predictor.set_image(np.ascontiguousarray(source_rgb[:, :, :3]))


def predict_box(
    predictor: Any,
    box_xyxy: tuple[int, int, int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Run the frozen box-only prompt and return (masks, scores).

    Masks is (C, H, W) bool over the encoded source image; scores is (C,).
    """

    masks, scores, _ = predictor.predict(
        box=np.array(box_xyxy, dtype=np.float32),
        multimask_output=True,
    )
    return np.asarray(masks).astype(bool), np.asarray(scores, dtype=np.float64)


def select_winner(scores: np.ndarray) -> int:
    """Frozen v0.1 winner rule: max SAM score (ties -> lowest index)."""

    if scores.size == 0:
        raise SamBackendError("SAM returned no candidates")
    return int(np.argmax(scores))


def close_3x3(mask: np.ndarray) -> np.ndarray:
    """Binary 3x3 full-ones close, one iteration (dilate 1px, erode 1px).

    Deterministic 8-neighbour morphology equivalent to cv2 morphology with a
    3x3 ones kernel; implemented with Pillow Max/Min filters so no OpenCV
    dependency is required.
    """

    from PIL import Image, ImageFilter

    image = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    dilated = image.filter(ImageFilter.MaxFilter(3))
    eroded = dilated.filter(ImageFilter.MinFilter(3))
    return np.asarray(eroded) > 127


def connected_components_8(mask: np.ndarray) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Label 8-connected components deterministically.

    Returns ``(components, label_image)``. Components are sorted by first
    appearance (top-to-bottom, left-to-right), each as
    ``{"label", "area", "bbox"}`` with ``bbox`` in ``{x, y, width, height}``
    form over the mask coordinate space. ``label_image`` holds the final
    component label per pixel (0 for background).
    """

    height, width = mask.shape
    provisional = np.zeros((height, width), dtype=np.int32)
    parent: list[int] = [0]
    previous_row_runs: list[tuple[int, int, int]] = []  # (x1, x2, label)

    def find(node: int) -> int:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for y in range(height):
        row = mask[y]
        if not row.any():
            previous_row_runs = []
            continue
        diffs = np.diff(np.concatenate(([False], row, [False])).astype(np.int8))
        starts = np.flatnonzero(diffs == 1)
        ends = np.flatnonzero(diffs == -1)
        current_row_runs: list[tuple[int, int, int]] = []
        for x1, x2 in zip(starts.tolist(), ends.tolist()):
            touched: list[int] = []
            for px1, px2, plabel in previous_row_runs:
                # 8-connectivity: runs in rows y-1 and y touch when
                # [x1-1, x2) overlaps the previous run [px1, px2).
                if x1 - 1 < px2 and px1 < x2:
                    touched.append(find(plabel))
            if touched:
                label = touched[0]
                for other in touched[1:]:
                    parent[other] = label
            else:
                label = len(parent)
                parent.append(label)
            provisional[y, x1:x2] = label
            current_row_runs.append((x1, x2, label))
        previous_row_runs = current_row_runs

    # Resolve unions and assign final labels in first-appearance order.
    remap: dict[int, int] = {}
    next_label = 1

    def final_label(node: int) -> int:
        nonlocal next_label
        root = find(node)
        if root not in remap:
            remap[root] = next_label
            next_label += 1
        return remap[root]

    label_image = np.zeros((height, width), dtype=np.int32)
    for y in range(height):
        row = provisional[y]
        if not row.any():
            continue
        diffs = np.diff(np.concatenate(([False], row > 0, [False])).astype(np.int8))
        starts = np.flatnonzero(diffs == 1)
        ends = np.flatnonzero(diffs == -1)
        for x1, x2 in zip(starts.tolist(), ends.tolist()):
            label_image[y, x1:x2] = final_label(int(row[x1]))

    components: list[dict[str, Any]] = []
    for label in range(1, next_label):
        ys, xs = np.nonzero(label_image == label)
        components.append(
            {
                "label": label,
                "area": int(ys.size),
                "bbox": {
                    "x": int(xs.min()),
                    "y": int(ys.min()),
                    "width": int(xs.max()) - int(xs.min()) + 1,
                    "height": int(ys.max()) - int(ys.min()) + 1,
                },
            }
        )
    return components, label_image


def filter_components(
    mask: np.ndarray,
    *,
    positive_points: list[tuple[int, int]] | None = None,
    relative_threshold: float = RELATIVE_COMPONENT_THRESHOLD,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Frozen component filter over 8-connected components.

    Rules: every component containing a positive point is kept; when no
    component contains a positive point, the largest component is kept;
    additionally every component with ``area >= largest_area *
    relative_threshold`` is kept. With no points (the v0.1 case) this reduces
    to "largest + components >= 8% of largest".
    """

    if relative_threshold < 0:
        raise ValueError("relative_threshold must be >= 0")
    components, label_image = connected_components_8(mask)
    if not components:
        empty_stats = {
            "connectivity": CONNECTIVITY,
            "relative_component_threshold": relative_threshold,
            "component_count": 0,
            "kept_component_count": 0,
            "removed_component_count": 0,
            "components": [],
            "kept_labels": [],
            "removed_components": [],
        }
        return np.zeros_like(mask), empty_stats

    points = list(positive_points or [])
    hit_labels: set[int] = set()
    for px, py in points:
        if 0 <= py < mask.shape[0] and 0 <= px < mask.shape[1] and mask[py, px]:
            hit_labels.add(int(label_image[py, px]))

    largest = max(components, key=lambda component: component["area"])
    area_floor = largest["area"] * relative_threshold
    kept_labels: set[int] = set(hit_labels)
    for component in components:
        if component["area"] >= area_floor:
            kept_labels.add(component["label"])

    keep = np.isin(label_image, sorted(kept_labels))
    kept_components = [
        component for component in components if component["label"] in kept_labels
    ]
    removed = [component for component in components if component["label"] not in kept_labels]
    stats = {
        "connectivity": CONNECTIVITY,
        "relative_component_threshold": relative_threshold,
        "component_count": len(components),
        "kept_component_count": len(kept_components),
        "removed_component_count": len(removed),
        "largest_component_area": largest["area"],
        "area_threshold_pixels": area_floor,
        "components": components,
        "kept_labels": sorted(kept_labels),
        "removed_components": removed,
    }
    return keep, stats


def postprocess_sam_mask(
    mask: np.ndarray,
    *,
    positive_points: list[tuple[int, int]] | None = None,
    relative_threshold: float = RELATIVE_COMPONENT_THRESHOLD,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Frozen v0.1 postprocess: 3x3 close then 8-connected component filter."""

    closed = close_3x3(mask)
    filtered, filter_stats = filter_components(
        closed,
        positive_points=positive_points,
        relative_threshold=relative_threshold,
    )
    stats = {
        "close": {
            "kernel": CLOSE_KERNEL,
            "iterations": CLOSE_ITERATIONS,
            "description": "dilate 1px then erode 1px",
        },
        "pixels": {
            "sam_winner": int(mask.sum()),
            "after_close": int(closed.sum()),
            "after_filter": int(filtered.sum()),
            "close_delta": int(closed.sum()) - int(mask.sum()),
            "filter_delta": int(filtered.sum()) - int(closed.sum()),
        },
        "connected_components": filter_stats,
    }
    return filtered, stats


def build_candidates_metadata(
    masks: np.ndarray,
    scores: np.ndarray,
) -> list[dict[str, Any]]:
    """Compact per-candidate diagnostics mirroring the frozen experiment."""

    candidates = []
    for index, (mask, score) in enumerate(zip(masks, scores)):
        ys, xs = np.nonzero(mask)
        if ys.size:
            mask_bbox = {
                "x1": int(xs.min()),
                "y1": int(ys.min()),
                "x2": int(xs.max()) + 1,
                "y2": int(ys.max()) + 1,
            }
        else:
            mask_bbox = None
        candidates.append(
            {
                "index": index,
                "sam_score": float(score),
                "mask_area": int(mask.sum()),
                "mask_bbox": mask_bbox,
            }
        )
    return candidates
