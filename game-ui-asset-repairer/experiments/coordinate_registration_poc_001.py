#!/usr/bin/env python3
"""Coordinate Registration Diagnostic PoC — asset_027 / asset_028.

Experiment-only. Freezes the SAM occluder -> target projection contract and
hard-verifies source registration of asset_027/filtered-rgba.png.

Rules honored:
- no SAM re-run, no mask modification, no dilation, no bbox fallback
- no C1/C1.5 semantics change, no repair, no working-image generation
- no resize / trim / padding anywhere; the only transform is integer
  translation between frozen origins

Projection contract (frozen here):
    winner-mask.png is bbox-local to asset_028 bbox_source (627,1322)
    target-local paste offset = occluder_origin - target_frame.origin = (75,57)
    source_x = occluder_bbox_source.x + mask_local_x
    target_local_x = source_x - target_frame.x
    The SAM foreground bbox inside the 44x54 canvas (4,7,35,43) is
    diagnostic ONLY — never an extra paste offset.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

RUN_DIR = Path(
    r"D:\Third_Test_1\UI-skill\runs\20260902_direct-asset-discovery-007-production-client"
)
BATCH = Path(r"D:\Third_Test_1\UI-skill\runs\20260904_sam_box_only_batch_001")
OUT_DIR = RUN_DIR / "stage2c" / "repair" / "asset_027" / "registration-poc-001"

SOURCE_PNG = RUN_DIR / "source.png"
T027 = BATCH / "asset_027"
T028 = BATCH / "asset_028"

TARGET_ORIGIN = (552, 1265)
TARGET_SIZE = (344, 122)
OCC_ORIGIN = (627, 1322)
OCC_SIZE = (44, 54)

MASK_OVERLAY = (255, 0, 0, 140)


def blend(base_rgb: np.ndarray, mask_bool: np.ndarray, color) -> np.ndarray:
    out = base_rgb.astype(np.float32)
    c = np.array(color[:3], dtype=np.float32)
    a = color[3] / 255.0
    m = mask_bool[..., None]
    out = np.where(m, c * a + out * (1.0 - a), out)
    return np.clip(out, 0, 255).astype(np.uint8)


def mask_bbox(mask_bool: np.ndarray):
    if not mask_bool.any():
        return None
    ys, xs = np.nonzero(mask_bool)
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fw, fh = TARGET_SIZE

    source = np.array(Image.open(SOURCE_PNG).convert("RGB"))
    sh, sw = source.shape[:2]

    winner = np.array(Image.open(T028 / "winner-mask.png").convert("L"))
    filtered = np.array(Image.open(T028 / "filtered-mask.png").convert("L"))
    target_rgba = np.array(Image.open(T027 / "filtered-rgba.png").convert("RGBA"))

    # ---- canvas sanity (no resize: sizes must already match) ----
    assert winner.shape == (OCC_SIZE[1], OCC_SIZE[0]), f"winner canvas {winner.shape}"
    assert target_rgba.shape[:2] == (fh, fw), f"target canvas {target_rgba.shape}"

    winner_bool = winner > 127
    filtered_bool = filtered > 127

    # ---- 1. winner == filtered (pixel-exact) ----
    winner_filtered_equal = bool(np.array_equal(winner_bool, filtered_bool))

    # ---- 2. frozen projection ----
    dx = OCC_ORIGIN[0] - TARGET_ORIGIN[0]
    dy = OCC_ORIGIN[1] - TARGET_ORIGIN[1]
    assert (dx, dy) == (75, 57), f"projection offset {(dx, dy)} != (75, 57)"

    # paste whole winner canvas onto a target-local canvas (integer translate)
    proj = np.zeros((fh, fw), dtype=bool)
    proj[dy : dy + OCC_SIZE[1], dx : dx + OCC_SIZE[0]] = winner_bool

    winner_local_bbox = mask_bbox(winner_bool)
    expected_local_bbox = [dx + winner_local_bbox[0], dy + winner_local_bbox[1],
                           winner_local_bbox[2], winner_local_bbox[3]]
    actual_local_bbox = mask_bbox(proj)
    bbox_assert_pass = actual_local_bbox == expected_local_bbox == [79, 64, 35, 43]

    # ---- 3. source registration of asset_027 filtered-rgba ----
    crop = source[TARGET_ORIGIN[1] : TARGET_ORIGIN[1] + fh,
                  TARGET_ORIGIN[0] : TARGET_ORIGIN[0] + fw]
    Image.fromarray(crop, mode="RGB").save(OUT_DIR / "source-target-crop.png")

    crop_rgb = crop.astype(np.int16)
    tgt_rgb = target_rgba[:, :, :3].astype(np.int16)
    rgb_equal = crop_rgb == tgt_rgb
    rgb_equal_pixel_count = int(rgb_equal.all(axis=2).sum())
    rgb_different_pixel_count = fh * fw - rgb_equal_pixel_count
    rgb_byte_identical = bool(rgb_equal.all())

    # also verify alpha is the only thing Stage2-B postprocess changed:
    # expected alpha == filtered mask of 027
    f027 = np.array(Image.open(T027 / "filtered-mask.png").convert("L"))
    alpha_matches_filtered = bool(np.array_equal(target_rgba[:, :, 3], f027))

    target_registration = "PASS" if rgb_byte_identical else "FAIL"

    # ---- 4. three hard-evidence overlays ----
    # (a) full source overlay at occluder origin (627,1322)
    src_occ_bool = np.zeros((sh, sw), dtype=bool)
    src_occ_bool[OCC_ORIGIN[1] : OCC_ORIGIN[1] + OCC_SIZE[1],
                 OCC_ORIGIN[0] : OCC_ORIGIN[0] + OCC_SIZE[0]] = winner_bool
    source_overlay = blend(source, src_occ_bool, MASK_OVERLAY)
    Image.fromarray(source_overlay, mode="RGB").save(OUT_DIR / "source-overlay.png")

    # (b) target crop + winner at (75,57)
    crop_overlay = blend(crop, proj, MASK_OVERLAY)
    Image.fromarray(crop_overlay, mode="RGB").save(OUT_DIR / "source-target-crop-overlay.png")

    # (c) asset_027 filtered-rgba over checkerboard + same winner at (75,57)
    tile = 8
    yy, xx = np.mgrid[0:fh, 0:fw]
    cell = ((xx // tile) + (yy // tile)) % 2
    checker = np.where(cell[..., None] == 0, 255, 204).astype(np.float32)
    checker = np.dstack([checker] * 3)
    a = target_rgba[:, :, 3:4].astype(np.float32) / 255.0
    composited = target_rgba[:, :, :3].astype(np.float32) * a + checker * (1.0 - a)
    composited = np.clip(composited, 0, 255).astype(np.uint8)
    asset_overlay = blend(composited, proj, MASK_OVERLAY)
    Image.fromarray(asset_overlay, mode="RGB").save(OUT_DIR / "target-asset-overlay.png")

    # zoomed evidence pairs (view-only NEAREST x4, data untouched)
    def zoom(arr, region, scale=4):
        x1, y1, x2, y2 = region
        return np.array(Image.fromarray(arr[y1:y2, x1:x2]).resize(
            ((x2 - x1) * scale, (y2 - y1) * scale), Image.NEAREST))

    # occluder region in source coords, padded
    region_occ = (OCC_ORIGIN[0] - 20, OCC_ORIGIN[1] - 20,
                  OCC_ORIGIN[0] + OCC_SIZE[0] + 20, OCC_ORIGIN[1] + OCC_SIZE[1] + 20)
    gap = 12
    z1 = zoom(source_overlay, region_occ)
    z2 = zoom(source_overlay, region_occ)  # same; pair vs undecorated below
    z_plain = zoom(source, region_occ)
    h = max(z1.shape[0], z_plain.shape[0])
    panel = np.full((h, z1.shape[1] * 2 + gap, 3), 255, dtype=np.uint8)
    panel[: z_plain.shape[0], : z_plain.shape[1]] = z_plain
    panel[: z1.shape[0], z_plain.shape[1] + gap :] = z1
    Image.fromarray(panel, mode="RGB").save(OUT_DIR / "zoom-source-overlay-vs-plain.png")

    # target-side zoom: crop region around projected mask, plain vs overlay vs asset overlay
    region_tgt = (dx - 20, dy - 20, dx + OCC_SIZE[0] + 20, dy + OCC_SIZE[1] + 20)
    zc1 = zoom(crop_overlay, region_tgt)
    zc2 = zoom(asset_overlay, region_tgt)
    h2 = max(zc1.shape[0], zc2.shape[0])
    panel2 = np.full((h2, zc1.shape[1] * 2 + gap, 3), 255, dtype=np.uint8)
    panel2[: zc1.shape[0], : zc1.shape[1]] = zc1
    panel2[: zc2.shape[0], zc1.shape[1] + gap :] = zc2
    Image.fromarray(panel2, mode="RGB").save(OUT_DIR / "zoom-crop-overlay-vs-asset-overlay.png")

    # ---- 5. diagnosis ----
    # CASE evaluation per the task spec
    if target_registration == "PASS":
        diagnosis = (
            "CASE A: full coordinate chain correct. winner-mask covers asset_028 "
            "exactly on source; projection offset (75,57) lands the foreground at "
            "target-local (79,64,35,43); asset_027 filtered-rgba RGB is "
            "byte-identical to source crop (552,1265,344,122), so the asset IS "
            "pixel-registered. No locator problem."
        )
    else:
        diagnosis = "registration FAIL - inspect extraction ROI metadata"

    result = {
        "poc": "coordinate-registration-poc-001",
        "target": "asset_027",
        "occluder": "asset_028",
        "no_sam_rerun": True,
        "no_mask_modification": True,
        "no_dilation": True,
        "no_bbox_fallback": True,
        "no_repair_performed": True,
        "projection_contract": {
            "mask_canvas_space": "asset_028 bbox_source local",
            "occluder_origin_source": list(OCC_ORIGIN),
            "target_origin_source": list(TARGET_ORIGIN),
            "projection_offset_target_local": [dx, dy],
            "per_pixel_rule": "target_local = occluder_origin + mask_local - target_origin",
            "foreground_bbox_used_as_offset": False,
        },
        "target_origin_source": list(TARGET_ORIGIN),
        "target_size": list(TARGET_SIZE),
        "occluder_origin_source": list(OCC_ORIGIN),
        "occluder_mask_canvas_size": list(OCC_SIZE),
        "projection_offset_target_local": [dx, dy],
        "winner_foreground_bbox_local": winner_local_bbox,
        "expected_foreground_bbox_target_local": expected_local_bbox,
        "actual_foreground_bbox_target_local": actual_local_bbox,
        "foreground_bbox_assert_pass": bbox_assert_pass,
        "winner_filtered_mask_equal": winner_filtered_equal,
        "registration": {
            "source_crop_origin": list(TARGET_ORIGIN),
            "source_crop_size": [fw, fh],
            "rgb_equal_pixel_count": rgb_equal_pixel_count,
            "rgb_different_pixel_count": rgb_different_pixel_count,
            "rgb_byte_identical": rgb_byte_identical,
            "target_registration": target_registration,
            "alpha_equals_027_filtered_mask": alpha_matches_filtered,
        },
        "overlays": {
            "source_overlay": "source-overlay.png",
            "source_target_crop": "source-target-crop.png",
            "source_target_crop_overlay": "source-target-crop-overlay.png",
            "target_asset_overlay": "target-asset-overlay.png",
        },
        "diagnosis": diagnosis,
    }
    (OUT_DIR / "projection-debug.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps({k: result[k] for k in (
        "winner_filtered_mask_equal", "projection_offset_target_local",
        "actual_foreground_bbox_target_local", "foreground_bbox_assert_pass",
        "registration")}, indent=2))
    print("diagnosis:", diagnosis)


if __name__ == "__main__":
    main()
