#!/usr/bin/env python3
"""CV repair PoC 001 — asset_027 traditional-CV first-pass repair experiment.

Experiment-only. Does NOT touch production Stage2-C code, C1, C1.5, or any
authoritative repair input. Reads:

    repair-working-image.png  (authoritative, RGB blacked inside mask)
    repair-mask.png           (authoritative, binary, WHITE = repair)

and writes results to cv-repair-poc-001/ next to the inputs.

Alpha policy (audited decision, stated explicitly):
    RGB      -> repaired by cv2.inpaint (Telea / Navier-Stokes) on the 3
                RGB channels only, using repair-mask.png as the inpaint mask.
    Alpha    -> NEVER passed to inpaint. Copied byte-identically from the
                authoritative target RGBA (repair-working-image.png alpha is
                byte-identical to the Stage2-B original by C1.5 contract),
                so the mask region and everything outside it keep the exact
                original alpha. Only RGB inside the mask changes.

cv2.inpaint does not support the RGBA semantics; feeding 4 channels would
inpaint alpha as a third color channel and silently corrupt transparency.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

RUN_DIR = Path(
    r"D:\Third_Test_1\UI-skill\runs\20260902_direct-asset-discovery-007-production-client"
)
REPAIR_DIR = RUN_DIR / "stage2c" / "repair" / "asset_027"
OUT_DIR = REPAIR_DIR / "cv-repair-poc-001"

RADII = [1, 2, 3, 5]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    working_rgba = np.array(
        Image.open(REPAIR_DIR / "repair-working-image.png").convert("RGBA")
    )
    original_rgba = np.array(
        Image.open(
            Path(
                "D:/Third_Test_1/UI-skill/runs/20260904_sam_box_only_batch_001/asset_027/filtered-rgba.png"
            )
        ).convert("RGBA")
    )
    mask_img = Image.open(REPAIR_DIR / "repair-mask.png")
    # binary mask: WHITE = repair. Normalize to {0, 255} uint8 regardless of
    # source mode (L or RGBA).
    mask_gray = np.array(mask_img.convert("L"))
    binary_mask = ((mask_gray >= 128).astype(np.uint8)) * 255

    h, w = binary_mask.shape
    assert working_rgba.shape[:2] == (h, w), "working image / mask size mismatch"
    assert original_rgba.shape[:2] == (h, w), "original / mask size mismatch"

    mask_bool = binary_mask > 0
    repair_pixel_count = int(mask_bool.sum())

    rgb = working_rgba[:, :, :3].copy()
    alpha = working_rgba[:, :, 3].copy()

    # cv2.inpaint wants BGR 3-channel + 8-bit single-channel mask
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    results = []
    for method_id, method_name in ((cv2.INPAINT_TELEA, "telea"), (cv2.INPAINT_NS, "ns")):
        for radius in RADII:
            repaired_bgr = cv2.inpaint(bgr, binary_mask, radius, method_id)
            repaired_rgb = cv2.cvtColor(repaired_bgr, cv2.COLOR_BGR2RGB)

            # Compose: alpha byte-identical; RGB changed ONLY inside mask.
            out_rgba = np.dstack([repaired_rgb, alpha])
            final = np.where(mask_bool[:, :, None], out_rgba, working_rgba)

            assert (final[:, :, 3] == working_rgba[:, :, 3]).all(), "alpha must be untouched"
            assert (final[~mask_bool] == working_rgba[~mask_bool]).all(), "outside mask must be untouched"

            if method_name == "telea":
                name = f"repaired-telea-r{radius}.png"
            else:
                name = f"repaired-ns-r{radius}.png"
            Image.fromarray(final, mode="RGBA").save(OUT_DIR / name)

            inside_changed = int((final[:, :, :3][mask_bool] != working_rgba[:, :, :3][mask_bool]).any(axis=1).sum())
            outside_changed = int((final[:, :, :3][~mask_bool] != working_rgba[:, :, :3][~mask_bool]).any(axis=1).sum())

            results.append(
                {
                    "method": method_name,
                    "cv_flag": int(method_id),
                    "radius": radius,
                    "output_file": name,
                    "output_size": [w, h],
                    "repair_pixel_count": repair_pixel_count,
                    "mask_inside_changed_pixel_count": inside_changed,
                    "mask_outside_changed_pixel_count": outside_changed,
                }
            )

    # Primary deliverable: Telea r=3 (middle-of-road default for tiny holes)
    primary = OUT_DIR / "repaired-cv.png"
    primary_t = OUT_DIR / "repaired-telea-r3.png"
    primary_t.replace(primary)
    for r in results:
        if r["output_file"] == "repaired-telea-r3.png":
            r["output_file"] = "repaired-cv.png (primary, moved)"
            r["note"] = "copied to repaired-cv.png as primary result"

    # Four-panel preview: original | working | mask | repaired (primary)
    panel_h, panel_w = h, w
    scale = 3  # asset is tiny (344x122), upscale x3 for inspection
    big = lambda arr: cv2.resize(arr, (panel_w * scale, panel_h * scale), interpolation=cv2.INTER_NEAREST)
    gap = 12

    orig_big = big(original_rgba)
    work_big = big(working_rgba)
    mask_vis = np.dstack([binary_mask] * 3)
    mask_big = big(mask_vis)
    rep_big = big(np.array(Image.open(primary).convert("RGBA")))

    canvas = np.full(
        (panel_h * scale * 2 + gap * 3, panel_w * scale * 2 + gap * 3, 3),
        255,
        dtype=np.uint8,
    )
    positions = [(gap, gap), (gap, panel_w * scale + gap * 2),
                 (panel_h * scale + gap * 2, gap), (panel_h * scale + gap * 2, panel_w * scale + gap * 2)]
    for img, (y, x) in zip((orig_big, work_big, mask_big, rep_big), positions):
        canvas[y : y + panel_h * scale, x : x + panel_w * scale] = img[:, :, :3]

    Image.fromarray(canvas, mode="RGB").save(OUT_DIR / "preview.png")

    # Copy authoritative inputs into the PoC dir for a self-contained record
    Image.open(REPAIR_DIR / "repair-working-image.png").save(OUT_DIR / "repair-working-image.png")
    Image.open(REPAIR_DIR / "repair-mask.png").save(OUT_DIR / "repair-mask.png")
    Image.open(
        Path("D:/Third_Test_1/UI-skill/runs/20260904_sam_box_only_batch_001/asset_027/filtered-rgba.png")
    ).save(OUT_DIR / "original.png")

    result = {
        "poc": "cv-repair-poc-001",
        "target": "asset_027",
        "occluder": "asset_028",
        "schema": "repair-input-v0.2 (authoritative, unmodified)",
        "algorithm": "cv2.inpaint (traditional CV, no image model, no VLM, no SAM)",
        "alpha_policy": {
            "rgb": "inpainted on 3 RGB channels only, mask = repair-mask.png",
            "alpha": "copied byte-identically from authoritative RGBA; never passed to inpaint",
        },
        "input": {
            "repair_input": str(REPAIR_DIR / "repair-input.json"),
            "repair_mask": str(REPAIR_DIR / "repair-mask.png"),
            "repair_working_image": str(REPAIR_DIR / "repair-working-image.png"),
            "repair_pixel_count": repair_pixel_count,
            "target_size": [w, h],
        },
        "variants": results,
        "primary": {
            "file": "repaired-cv.png",
            "method": "telea",
            "radius": 3,
            "output_size": [w, h],
            "mask_inside_changed_pixel_count": next(
                r["mask_inside_changed_pixel_count"]
                for r in results
                if r["method"] == "telea" and r["radius"] == 3
            ),
            "mask_outside_changed_pixel_count": 0,
        },
        "guardrails": {
            "no_sam_rerun": True,
            "no_bbox_recompute": True,
            "no_mask_modification": True,
            "no_vlm": True,
            "no_image2": True,
            "production_chain_untouched": True,
        },
    }
    (OUT_DIR / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps({k: result[k] for k in ("poc", "target", "primary")}, indent=2))
    for r in results:
        print(
            f"{r['method']:>5} r={r['radius']}  inside_changed={r['mask_inside_changed_pixel_count']:>5}"
            f"  outside_changed={r['mask_outside_changed_pixel_count']}"
        )


if __name__ == "__main__":
    main()
