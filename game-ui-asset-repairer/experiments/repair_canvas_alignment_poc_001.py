#!/usr/bin/env python3
"""Repair Canvas / Debug Preview PoC — asset_027 alignment verification.

Experiment-only. Does NOT touch C1.5, repair-mask semantics, or any
production code. No repair runs here (no cv2.inpaint, no Image 2).

Purpose: verify that repair-mask.png, repair-working-image.png and the
target asset truly share one raster coordinate system (target-local,
frozen by target_frame), and produce visually comparable previews.

Rules enforced by assertion:
- canvas = target_frame.width x target_frame.height, frozen
- no crop / trim-transparent / resize / fit-to-content anywhere
- working image composited with standard "over" alpha onto checkerboard
- mask overlay = semi-transparent red where mask > 0
- source-space preview uses the ONLY permitted transform:
      source_x = target_frame.x + local_x
      source_y = target_frame.y + local_y
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

RUN_DIR = Path(
    r"D:\Third_Test_1\UI-skill\runs\20260902_direct-asset-discovery-007-production-client"
)
REPAIR_DIR = RUN_DIR / "stage2c" / "repair" / "asset_027"
OUT_DIR = REPAIR_DIR / "alignment-poc-001"

TARGET_ASSET = Path(
    r"D:\Third_Test_1\UI-skill\runs\20260904_sam_box_only_batch_001\asset_027\filtered-rgba.png"
)
SOURCE_PNG = RUN_DIR / "source.png"

CHECKER_TILE = 8
CHECKER_A = (255, 255, 255, 255)
CHECKER_B = (204, 204, 204, 255)
MASK_OVERLAY_RGBA = (255, 0, 0, 128)  # semi-transparent red, "over" composite


def checkerboard(w: int, h: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    cell = ((xx // CHECKER_TILE) + (yy // CHECKER_TILE)) % 2
    rgb = np.where(cell[..., None] == 0, np.array(CHECKER_A[:3]), np.array(CHECKER_B[:3]))
    return rgb.astype(np.uint8)


def alpha_over(rgb_bg: np.ndarray, rgba_fg: np.ndarray) -> np.ndarray:
    """Standard source-over: out = fg*a + bg*(1-a). Background opaque."""
    a = rgba_fg[:, :, 3:4].astype(np.float32) / 255.0
    fg = rgba_fg[:, :, :3].astype(np.float32)
    bg = rgb_bg.astype(np.float32)
    out = fg * a + bg * (1.0 - a)
    return np.clip(out, 0, 255).astype(np.uint8)


def mask_bbox(mask_bool: np.ndarray) -> dict | None:
    if not mask_bool.any():
        return None
    ys, xs = np.nonzero(mask_bool)
    return {
        "x": int(xs.min()),
        "y": int(ys.min()),
        "width": int(xs.max() - xs.min() + 1),
        "height": int(ys.max() - ys.min() + 1),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    repair_input = json.loads((REPAIR_DIR / "repair-input.json").read_text(encoding="utf-8"))
    frame = repair_input["target_frame"]
    fw, fh = int(frame["width"]), int(frame["height"])

    target_rgba = np.array(Image.open(TARGET_ASSET).convert("RGBA"))
    working_rgba = np.array(Image.open(REPAIR_DIR / "repair-working-image.png").convert("RGBA"))
    mask_l = np.array(Image.open(REPAIR_DIR / "repair-mask.png").convert("L"))

    mask_bool = mask_l > 0
    fg_count = int(mask_bool.sum())
    fg_bbox = mask_bbox(mask_bool)

    # ---- asserts: every layer must already match the frozen canvas ----
    checks = {
        "target_asset_size_equals_frame": target_rgba.shape[:2] == (fh, fw),
        "repair_mask_size_equals_frame": mask_l.shape == (fh, fw),
        "working_image_size_equals_frame": working_rgba.shape[:2] == (fh, fw),
        "target_asset_size_equals_working_size": (
            target_rgba.shape == working_rgba.shape
        ),
    }
    all_pass = all(checks.values())
    if not all_pass:
        raise AssertionError(f"size asserts failed: {checks}")

    # ---- 1. target-local alignment preview (checkerboard + over composite) ----
    checker = checkerboard(fw, fh)
    canvas_local = alpha_over(checker, working_rgba)

    # semi-transparent red overlay where mask > 0, standard over on top
    overlay = canvas_local.astype(np.float32)
    ov_rgb = np.array(MASK_OVERLAY_RGBA[:3], dtype=np.float32)
    ov_a = MASK_OVERLAY_RGBA[3] / 255.0
    region = mask_bool[..., None]
    overlay = np.where(region, ov_rgb * ov_a + overlay * (1.0 - ov_a), overlay)
    alignment_preview = np.clip(overlay, 0, 255).astype(np.uint8)

    # side-by-side panel: [ working on checker | checker + red mask overlay ]
    gap = 12
    panel = np.full((fh, fw * 2 + gap, 3), 255, dtype=np.uint8)
    panel[:, :fw] = canvas_local
    panel[:, fw + gap :] = alignment_preview
    Image.fromarray(panel, mode="RGB").save(OUT_DIR / "alignment-preview.png")

    # ---- 2. source-space preview (only permitted transform: translate) ----
    src = np.array(Image.open(SOURCE_PNG).convert("RGB"))
    sh, sw = src.shape[:2]
    ox, oy = int(frame["x"]), int(frame["y"])

    # bounds check before paste (still no resize/crop of content itself)
    in_bounds = (ox >= 0) and (oy >= 0) and (ox + fw <= sw) and (oy + fh <= sh)
    if not in_bounds:
        raise AssertionError(f"target_frame does not fit inside source: {frame} vs {sw}x{sh}")

    src_debug = src.copy()
    # paste working image over the source screenshot region (alpha composite)
    region_rgba = working_rgba
    src_debug[oy : oy + fh, ox : ox + fw] = alpha_over(src_debug[oy : oy + fh, ox : ox + fw], region_rgba)
    # then overlay the mask in semi-transparent red on the same location
    m_rgb = np.array((255, 0, 0), dtype=np.float32)
    m_a = 128 / 255.0
    sub = src_debug[oy : oy + fh, ox : ox + fw].astype(np.float32)
    sub = np.where(mask_bool[..., None], m_rgb * m_a + sub * (1.0 - m_a), sub)
    src_debug[oy : oy + fh, ox : ox + fw] = np.clip(sub, 0, 255).astype(np.uint8)

    # annotated pair: original source region vs debug region, same crop box,
    # same scale — crop here is only for the debug VIEW, never for data.
    orig_region = src[oy : oy + fh, ox : ox + fw]
    dbg_region = src_debug[oy : oy + fh, ox : ox + fw]
    scale = 3
    big = lambda arr: np.array(
        Image.fromarray(arr).resize((fw * scale, fh * scale), Image.NEAREST)
    )
    view = np.full((fh * scale * 2 + gap, fw * scale, 3), 255, dtype=np.uint8)
    view[: fh * scale] = big(orig_region)
    view[fh * scale + gap :] = big(dbg_region)
    Image.fromarray(view, mode="RGB").save(OUT_DIR / "source-space-preview.png")
    Image.fromarray(src_debug, mode="RGB").save(OUT_DIR / "source-space-full-canvas.png")

    # ---- 3. coordinate consistency verification ----
    # The only meaningful machine check: mask pixels vs working-image
    # "blacked" pixels must coincide exactly (C1.5 contract: RGB=0 inside
    # mask, byte-identical outside). If they coincide, the two files share
    # one raster coordinate system.
    blacked = (working_rgba[:, :, :3] == 0).all(axis=2)
    mask_vs_blacked_equal = bool((blacked == mask_bool).all())
    blacked_count = int(blacked.sum())
    blacked_outside_mask = int((blacked & ~mask_bool).sum())
    mask_pixels_not_blacked = int((mask_bool & ~blacked).sum())

    result = {
        "poc": "repair-canvas-alignment-poc-001",
        "target": "asset_027",
        "no_repair_performed": True,
        "cv2_inpaint_called": False,
        "image2_called": False,
        "c15_semantics_modified": False,
        "canvas": {
            "size": [fw, fh],
            "coordinate_space": "target_local",
            "frozen_from": "repair-input.json target_frame",
        },
        "source_origin": {"x": int(frame["x"]), "y": int(frame["y"])},
        "sizes": {
            "target_frame": [fw, fh],
            "target_asset": list(target_rgba.shape[1::-1]),
            "repair_mask": list(mask_l.shape[::-1]),
            "working_image": list(working_rgba.shape[1::-1]),
            "source_screenshot": [sw, sh],
        },
        "asserts": {**checks, "all_pass": all_pass},
        "mask_foreground": {"pixel_count": fg_count, "local_bbox": fg_bbox},
        "coordinate_checks": {
            "mask_pixels_equal_blacked_pixels": mask_vs_blacked_equal,
            "blacked_pixel_count": blacked_count,
            "mask_pixel_count": fg_count,
            "blacked_outside_mask_count": blacked_outside_mask,
            "mask_pixels_not_blacked_count": mask_pixels_not_blacked,
        },
        "outputs": {
            "alignment_preview": "alignment-preview.png",
            "source_space_preview": "source-space-preview.png",
            "source_space_full_canvas": "source-space-full-canvas.png",
        },
    }
    (OUT_DIR / "alignment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
