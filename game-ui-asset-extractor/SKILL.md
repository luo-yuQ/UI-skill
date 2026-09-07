---
name: game-ui-asset-extractor
description: Deterministically extract reusable PNG assets from frozen Stage2-A final bboxes using either exact direct cropping, local-background foreground extraction, or the frozen SAM1 ViT-B box-only backend (encode source once, max-SAM-score multimask winner, 3x3 close, 8-connected 8% component filter). Use for Stage2-B asset extraction from game UI screenshots when source-image pixel bboxes are already finalized; do not use for asset discovery, classification, bbox refinement, inpainting, redraw, normalization, or extraction quality scoring.
---

# Game UI Asset Extractor

Extract only pixels that already exist in the source screenshot. Treat every input `final_bbox` as immutable Stage2-A output in original-image coordinates.

## Workflow

1. Create a request matching `schemas/extraction-request.schema.json`.
2. Set `extraction_mode` explicitly for every asset:
   - Use `direct_crop` for rectangular panels, slots, and other assets whose bbox is the desired canvas.
   - Use `foreground_extract` when the asset needs a transparent background.
3. Pick the backend via `config.backend` (default `pillow`; `sam1_vit_b` for the frozen SAM baseline).
4. Run:

```powershell
# Legacy pillow backend (unchanged)
python scripts/extract_assets.py --request path\to\extraction-request.json --output-dir path\to\extraction

# Frozen SAM v0.1 backend (checkpoint supplied explicitly, never auto-downloaded)
python scripts/extract_assets.py --request path\to\extraction-request.json --output-dir path\to\extraction --backend sam1_vit_b --sam-checkpoint path\to\sam_vit_b_01ec64.pth
```

5. Read `extraction-result.json`. Treat only explicit program failures as `failed`; Stage2-D owns visual quality review. Write foreground assets to `assets/<asset_id>.png`, binary debug masks to `masks/<asset_id>_mask.png`, and traceable parameters and coordinates to `extraction-result.json`.

## Foreground extraction contract (pillow backend)

Build a clamped ROI by expanding `final_bbox` with the configured fixed padding. Define the Context Ring as pixels inside that ROI but outside `final_bbox`. Use the ring only to estimate local background; never classify ring pixels as target foreground.

Estimate RGB background with the ring median. If too few ring pixels exist, explicitly fall back to the ROI border median, then the source-image border median. Generate `color_distance_v0` by thresholding Euclidean RGB distance inside the final-bbox core. Apply deterministic Pillow close/open morphology, optional dilation, and Gaussian blur. Multiply generated alpha by source alpha and emit straight-alpha RGBA without changing source RGB.

## SAM v0.1 baseline contract (sam1_vit_b backend, frozen)

SAM1 ViT-B with the reviewed `final_bbox` (source-image pixel coordinates) as a box prompt against the **full source image**, which is encoded exactly once per request (`predictor.set_image`); each asset then runs `predict(box=..., multimask_output=True)`. The winner is the candidate with the max SAM score. The winner mask is postprocessed deterministically: 3x3 full-ones binary close (1 iteration), then 8-connected component filtering that keeps the largest component plus every component >= 8% of its area (the point-hit rule exists in code but is always empty in v0.1 because there are no points). Final RGB comes from untouched source pixels; alpha = postprocessed mask * source alpha. Per-asset diagnostics (model, prompt, candidates, winner index/score, postprocess parameters, mask area, component counts) are written to `mask_parameters`.

Frozen decisions — do not "improve" them without a new frozen baseline:

- Point prompting: NOT part of v0.1 baseline. Reserved for future fallback.
- Geometry-aware candidate scoring: evaluated but not adopted in v0.1.
- Occlusion / clean-base repair: belongs to Stage2-C and is out of scope.

The SAM backend requires torch + `segment-anything` and an explicitly provided ViT-B checkpoint (`--sam-checkpoint` / `config.sam_checkpoint`); checkpoints are never auto-downloaded or committed. Device is `auto` (CUDA when available, else CPU with a recorded fallback), `cuda`, or `cpu`. Failures (missing package, missing/unloadable checkpoint, invalid bbox, empty mask, no candidates) are explicit and never silently fall back to another segmentation algorithm.

## Direct crop contract

Crop exactly `final_bbox`, preserve source RGB and alpha, and skip ROI padding, background estimation, mask creation, morphology, and alpha generation. This contract is identical under both backends.

## Boundaries

Never:

- alter or replace `final_bbox`;
- discover, split, classify, deduplicate, or retry assets;
- use generated pixels, inpainting, redraw, alpha matting, halo repair, or edge decontamination;
- encode tight crops instead of the full source image, re-encode the source per asset, or pass point prompts in the v0.1 SAM baseline;
- emit `review_required`, quality scores, or Stage2-C normalization.

If alpha bounds are inspected downstream, record them as extraction metadata only. Do not write them back as a new bbox.

