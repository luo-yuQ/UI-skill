#!/usr/bin/env python3
"""Stage2-C Traditional CV Repair v0.1 (production).

Frozen from smooth-plate-repair-poc-001 + dilation-policy-poc (2026-09-07).

Pipeline position: runs AFTER C1.5 repair-input preparation. Reads the
existing ``repair-input.json`` as the sole authoritative input — never
re-infers bboxes / masks / relations, never re-runs SAM / VLM / Image2.

Frozen algorithm (deterministic):
1. Repair mask safety dilation — per-occluder proportional rule:
       radius = clamp(round(min(bbox_w, bbox_h) * dilation_ratio), 1, 6)
   Each projected occluder mask is dilated by its own radius (binary
   morphology, cross structure), then unioned with the original mask.
   Stage2-B masks are never modified; this is a Stage2-C-only expansion.
2. RGB repair — per-channel low-order linear surface fit with robust
   IRLS-Huber on a guard-filtered sampling ring around the effective mask:
       color(x, y) = a + b*x + c*y
   No cv2.inpaint (TELEA/NS are explicitly banned — they smear dark
   strokes into smooth UI plates).
3. Alpha repair — deterministic consensus rule from the sampling ring:
   >=98% opaque -> 255, >=98% zero -> 0, otherwise nearest reliable pixel.
4. Write rule — only pixels inside the effective mask change; every pixel
   outside is byte-identical to the original (hard-asserted).

Scope: smooth / low-complexity UI surfaces (flat plates, simple gradients).
Whether an asset is suitable for CV repair is the future Router's job.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

REPAIRER_DIR = Path(__file__).resolve().parent
RESULT_SCHEMA_PATH = REPAIRER_DIR / "schemas" / "cv-repair-result.schema.json"

SCHEMA_VERSION = "cv-repair-result-v0.1"

# ---- frozen algorithm constants (smooth-plate-repair-poc-001) ----
RING_OUTER_PX = 8          # sampling ring outward expansion
SAMPLING_GUARD_RGB_DIST = 45.0   # robust pre-filter: max RGB distance from ring median
HUBER_DELTA = 12.0
IRLS_ITERS = 8
SURFACE_FIT_METHOD = "linear_irls_huber_v0.1"   # linear frozen; quadratic rejected (overfit blotches)
ALPHA_REPAIR_METHOD = "ring_consensus_or_nearest_v0.1"

# ---- frozen dilation policy (dilation-policy-poc + freeze spec) ----
DEFAULT_DILATION_RATIO = 0.05
DILATION_MIN_PX = 1
DILATION_MAX_PX = 6

MIN_SAMPLING_PIXELS = 12   # below this a 3-parameter linear fit is not trustworthy

REASON_OK = "ok"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def compute_radius_for_bbox(width: int, height: int, ratio: float,
                            min_px: int = DILATION_MIN_PX,
                            max_px: int = DILATION_MAX_PX) -> int:
    """Frozen proportional rule: clamp(round(min(w, h) * ratio), min_px, max_px)."""
    raw = min(int(width), int(height)) * ratio
    return int(max(min_px, min(round(raw), max_px)))


def project_occluder_mask(occluder_mask: np.ndarray,
                          occluder_bbox: dict[str, int],
                          target_frame: dict[str, int],
                          target_shape: tuple[int, int]) -> np.ndarray | None:
    """Deterministic source-pixel reprojection of an occluder mask into the
    target-local frame. Same arithmetic as C1.5 (no re-inference)."""
    oh, ow = occluder_mask.shape
    if ow != int(occluder_bbox["width"]) or oh != int(occluder_bbox["height"]):
        raise ValueError("occluder mask size does not match bbox_source")
    ofx, ofy = int(occluder_bbox["x"]), int(occluder_bbox["y"])
    tfx, tfy = int(target_frame["x"]), int(target_frame["y"])
    src_x1, src_y1 = ofx, ofy
    src_x2, src_y2 = ofx + ow, ofy + oh
    tx1, ty1 = tfx, tfy
    tx2, ty2 = tfx + int(target_frame["width"]), tfy + int(target_frame["height"])
    ix1, iy1 = max(src_x1, tx1), max(src_y1, ty1)
    ix2, iy2 = min(src_x2, tx2), min(src_y2, ty2)
    if ix1 >= ix2 or iy1 >= iy2:
        return None
    patch = occluder_mask[iy1 - src_y1:iy2 - src_y1, ix1 - src_x1:ix2 - src_x1]
    dst_x1, dst_y1 = ix1 - tfx, iy1 - tfy
    projected = np.zeros(target_shape, dtype=bool)
    projected[dst_y1:dst_y1 + patch.shape[0], dst_x1:dst_x1 + patch.shape[1]] = patch
    return projected


def fit_channel_linear(x: np.ndarray, y: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Per-channel linear fit with IRLS-Huber (frozen params)."""
    xx, yy = x.astype(np.float64), y.astype(np.float64)
    A = np.stack([np.ones_like(xx), xx, yy], axis=1)
    w = np.ones(len(values))
    coef = np.zeros(3)
    for _ in range(IRLS_ITERS):
        Aw = A * w[:, None]
        coef, *_ = np.linalg.lstsq(Aw.T @ A, Aw.T @ values, rcond=None)
        resid = values - A @ coef
        a = np.abs(resid)
        w = np.where(a <= HUBER_DELTA, 1.0, HUBER_DELTA / np.maximum(a, 1e-9))
    return coef


def build_sampling_region(orig: np.ndarray, effective_mask: np.ndarray):
    """Sampling ring around the effective mask + deterministic guard filter.

    Returns (sample_y, sample_x, guard_mask, stats)."""
    H, W = effective_mask.shape
    ring = ndimage.binary_dilation(effective_mask, iterations=RING_OUTER_PX) & (~effective_mask)
    ys, xs = np.nonzero(ring)
    if len(ys) == 0:
        return None, None, None, {"ring_pixels_before_guard": 0}
    ring_rgb = orig[ys, xs, :3].astype(np.float64)
    ring_alpha = orig[ys, xs, 3]

    guard_alpha = ring_alpha == 255
    if not guard_alpha.any():
        return None, None, None, {"ring_pixels_before_guard": int(ring.sum())}
    med = np.median(ring_rgb[guard_alpha], axis=0)
    dist = np.linalg.norm(ring_rgb - med[None, :], axis=1)
    guard = guard_alpha & (dist <= SAMPLING_GUARD_RGB_DIST)

    stats = {
        "ring_pixels_before_guard": int(ring.sum()),
        "guard_alpha_opaque": int(guard_alpha.sum()),
        "guard_color_passed": int(guard.sum()),
    }
    if not guard.any():
        return None, None, None, stats
    return ys[guard], xs[guard], guard, stats


def repair_alpha(orig: np.ndarray, effective_mask: np.ndarray,
                 sample_y: np.ndarray, sample_x: np.ndarray):
    """Deterministic alpha restore. Returns (alpha_fill, method_counts)."""
    H, W = effective_mask.shape
    ring_alpha = orig[sample_y, sample_x, 3]
    n0 = int((ring_alpha == 0).sum())
    nmid = int(((ring_alpha > 0) & (ring_alpha < 255)).sum())
    n255 = int((ring_alpha == 255).sum())
    ntot = max(len(ring_alpha), 1)
    consensus = 255 if n255 / ntot >= 0.98 else (0 if n0 / ntot >= 0.98 else None)
    if consensus is not None:
        alpha_fill = np.full((H, W), consensus, dtype=np.uint8)
    else:
        guard = np.zeros((H, W), dtype=bool)
        guard[sample_y, sample_x] = True
        _, (iy, ix) = ndimage.distance_transform_edt(~guard, return_indices=True)
        alpha_fill = orig[iy, ix, 3]
    counts = {"alpha_zero": n0, "alpha_partial": nmid, "alpha_opaque": n255}
    return alpha_fill, counts


def run_cv_repair(repair_input_path: Path, output_dir: Path,
                  dilation_ratio: float = DEFAULT_DILATION_RATIO,
                  dilation_px: int | None = None,
                  disable_dilation: bool = False,
                  debug: bool = False) -> dict[str, Any]:
    """Execute one deterministic CV repair. Always returns a result document;
    writes outputs only when status == success."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "status": "failed",
                              "reason_code": None, "message": None}

    def finish(status: str, reason_code: str, message: str | None = None) -> dict[str, Any]:
        result["status"] = status
        result["reason_code"] = reason_code
        result["message"] = message
        (output_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    # ---------- load authoritative input ----------
    try:
        inp = _load_json(repair_input_path)
    except (OSError, json.JSONDecodeError) as exc:
        return finish("failed", "repair_input_unreadable", str(exc))
    if inp.get("schema_version") != "repair-input-v0.2":
        return finish("failed", "invalid_repair_input",
                      f"unsupported schema_version: {inp.get('schema_version')}")
    if inp.get("status") != "success":
        return finish("skipped", "repair_input_not_success",
                      f"upstream status: {inp.get('status')} / {inp.get('reason_code')}")

    result["target_asset_id"] = inp["target_asset_id"]
    result["occluder_asset_ids"] = list(inp["occluder_asset_ids"])
    result["input_target_path"] = inp["target_asset_path"]
    result["input_repair_mask_path"] = inp["repair_mask_path"]
    result["output_path"] = None

    # ---------- load target + repair mask ----------
    orig = np.array(Image.open(inp["target_asset_path"]).convert("RGBA"))
    mask_img = np.array(Image.open(inp["repair_mask_path"]).convert("L"))
    H, W = orig.shape[:2]
    result["target_size"] = {"width": W, "height": H}
    if mask_img.shape != (H, W):
        return finish("failed", "mask_size_mismatch",
                      f"repair mask {mask_img.shape} != target {(H, W)}")
    rm = mask_img > 0
    result["repair_mask_original_pixels"] = int(rm.sum())
    if not rm.any():
        return finish("skipped", "empty_repair_mask", "repair mask has no pixels")

    # ---------- dilation ----------
    occluders_meta: list[dict[str, Any]] = []
    try:
        if disable_dilation:
            dilation_mode = "disabled"
            effective = rm.copy()
        elif dilation_px is not None:
            dilation_mode = "fixed_override"
            effective = ndimage.binary_dilation(rm, iterations=int(dilation_px))
        else:
            dilation_mode = "ratio"
            effective = rm.copy()  # hard guarantee: effective ⊇ original
            for occ in inp["occluder_masks"]:
                bbox = occ["bbox_source"]
                if bbox is None:
                    return finish("failed", "invalid_repair_input",
                                  f"occluder {occ['asset_id']} has null bbox_source")
                radius = compute_radius_for_bbox(bbox["width"], bbox["height"], dilation_ratio)
                om = np.array(Image.open(occ["mask_path"]).convert("L")) > 0
                projected = project_occluder_mask(om, bbox, inp["target_frame"], (H, W))
                if projected is None:
                    continue  # no overlap in frame (upstream guarantees overlap exists)
                effective |= ndimage.binary_dilation(projected, iterations=radius)
                occluders_meta.append({
                    "asset_id": occ["asset_id"],
                    "bbox_size": {"width": int(bbox["width"]), "height": int(bbox["height"])},
                    "min_dimension": int(min(bbox["width"], bbox["height"])),
                    "computed_radius_px": radius,
                    "effective_radius_px": radius,
                })
    except (OSError, ValueError) as exc:
        return finish("failed", "occluder_mask_shape_mismatch", str(exc))

    result["dilation_mode"] = dilation_mode
    result["repair_mask_dilation_ratio"] = None if disable_dilation else (
        None if dilation_px is not None else dilation_ratio)
    result["repair_mask_dilation_min_px"] = DILATION_MIN_PX
    result["repair_mask_dilation_max_px"] = DILATION_MAX_PX
    if occluders_meta:
        result["per_occluder_dilation"] = occluders_meta
    result["repair_mask_effective_pixels"] = int(effective.sum())

    # ---------- hard structural constraints ----------
    if effective.shape != (H, W):
        return finish("failed", "assertion_failed", "effective mask size != target size")
    if not (effective >= rm).all():
        return finish("failed", "assertion_failed",
                      "effective mask does not include original repair mask")
    if not effective.any():
        return finish("failed", "empty_repair_mask", "effective mask is empty after dilation")

    # ---------- sampling ----------
    sample_y, sample_x, guard, sampling_stats = build_sampling_region(orig, effective)
    if sample_y is None or len(sample_y) < MIN_SAMPLING_PIXELS:
        return finish("failed", "insufficient_sampling_pixels",
                      f"usable sampling pixels: {0 if sample_y is None else len(sample_y)}")
    result["sampling_parameters"] = {
        "ring_outer_px": RING_OUTER_PX,
        "guard_rgb_dist": SAMPLING_GUARD_RGB_DIST,
        "min_sampling_pixels": MIN_SAMPLING_PIXELS,
        **sampling_stats,
        "sampling_pixel_count": int(len(sample_y)),
    }

    # ---------- surface fit (RGB, per channel) ----------
    sample_rgb = orig[sample_y, sample_x, :3].astype(np.float64)
    yy, xx = np.mgrid[0:H, 0:W]
    A_all = np.stack([np.ones(xx.size), xx.ravel().astype(np.float64),
                      yy.ravel().astype(np.float64)], axis=1)
    fitted = np.zeros((H, W, 3), np.float64)
    for c in range(3):
        coef = fit_channel_linear(sample_y, sample_x, sample_rgb[:, c])
        fitted[..., c] = (A_all @ coef).reshape(H, W)
    fitted = np.clip(fitted, 0, 255)

    # ---------- alpha repair ----------
    alpha_fill, alpha_counts = repair_alpha(orig, effective, sample_y, sample_x)
    result["alpha_repair_method"] = ALPHA_REPAIR_METHOD
    result["sampling_alpha_zero_count"] = alpha_counts["alpha_zero"]
    result["sampling_alpha_partial_count"] = alpha_counts["alpha_partial"]
    result["sampling_alpha_opaque_count"] = alpha_counts["alpha_opaque"]

    # ---------- compose ----------
    final = orig.copy()
    final[..., :3][effective] = fitted[effective].astype(np.uint8)
    final[..., 3] = np.where(effective, alpha_fill, orig[..., 3])

    # ---------- verification metrics ----------
    inside_rgb = int((final[..., :3][effective] != orig[..., :3][effective]).any(axis=-1).sum())
    outside_rgb = int((final[..., :3][~effective] != orig[..., :3][~effective]).any(axis=-1).sum())
    inside_alpha = int((final[..., 3][effective] != orig[..., 3][effective]).sum())
    outside_alpha = int((final[..., 3][~effective] != orig[..., 3][~effective]).sum())
    result["repair_method"] = "deterministic_smooth_surface_fit_v0.1"
    result["surface_fit_method"] = SURFACE_FIT_METHOD
    result["mask_inside_rgb_changed"] = inside_rgb
    result["mask_outside_rgb_changed"] = outside_rgb
    result["mask_inside_alpha_changed"] = inside_alpha
    result["mask_outside_alpha_changed"] = outside_alpha

    # ---------- hard asserts ----------
    if final.shape != orig.shape:
        return finish("failed", "assertion_failed", "output size != target size")
    if outside_rgb != 0:
        return finish("failed", "assertion_failed",
                      f"mask_outside_rgb_changed = {outside_rgb} (must be 0)")
    if outside_alpha != 0:
        return finish("failed", "assertion_failed",
                      f"mask_outside_alpha_changed = {outside_alpha} (must be 0)")

    # ---------- outputs ----------
    repaired_path = output_dir / "repaired.png"
    effective_mask_path = output_dir / "repair-mask-effective.png"
    Image.fromarray(final, mode="RGBA").save(repaired_path, format="PNG", optimize=True)
    Image.fromarray(effective.astype(np.uint8) * 255, mode="L").save(
        effective_mask_path, format="PNG", optimize=True)
    result["output_path"] = repaired_path.as_posix()
    result["diagnostic_outputs"] = []

    if debug:
        prev = orig.copy()
        prev[effective] = [255, 0, 0, 255]
        prev[sample_y, sample_x] = [0, 255, 0, 255]
        sampling_path = output_dir / "sampling-preview.png"
        Image.fromarray(prev).save(sampling_path)
        result["diagnostic_outputs"] = [
            {"path": sampling_path.as_posix(), "role": "diagnostic"},
        ]

    return finish("success", REASON_OK, None)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage2-C Traditional CV Repair v0.1 (deterministic, smooth surfaces)")
    parser.add_argument("--repair-input", required=True,
                        help="Path to authoritative repair-input.json (C1.5 output)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--dilation-ratio", type=float, default=DEFAULT_DILATION_RATIO,
                        help=f"Safety dilation ratio (default {DEFAULT_DILATION_RATIO}); "
                             "radius = clamp(round(min(bbox_w,bbox_h)*ratio), 1, 6)")
    parser.add_argument("--dilation-px", type=int, default=None,
                        help="Fixed dilation radius in px (debug/manual override; "
                             "takes priority over --dilation-ratio)")
    parser.add_argument("--disable-dilation", action="store_true",
                        help="Test mode: effective mask == input mask (no dilation)")
    parser.add_argument("--debug", action="store_true",
                        help="Emit diagnostic artifacts (sampling-preview.png)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dilation_px is not None and args.dilation_px < 0:
        print("error: --dilation-px must be >= 0", file=sys.stderr)
        return 2
    result = run_cv_repair(
        Path(args.repair_input), Path(args.output_dir),
        dilation_ratio=args.dilation_ratio, dilation_px=args.dilation_px,
        disable_dilation=args.disable_dilation, debug=args.debug)
    print(json.dumps({k: result[k] for k in
                      ("schema_version", "status", "reason_code", "target_asset_id")},
                     ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
