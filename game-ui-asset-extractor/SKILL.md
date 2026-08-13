---
name: game-ui-asset-extractor
description: Deterministically extract reusable PNG assets from frozen Stage2-A final bboxes using either exact direct cropping or local-background foreground extraction with a context ring, binary color-distance mask, basic morphology, and soft alpha. Use for Stage2-B asset extraction from game UI screenshots when source-image pixel bboxes are already finalized; do not use for asset discovery, classification, bbox refinement, inpainting, redraw, normalization, or extraction quality scoring.
---

# Game UI Asset Extractor

Extract only pixels that already exist in the source screenshot. Treat every input `final_bbox` as immutable Stage2-A output in original-image coordinates.

## Workflow

1. Create a request matching `schemas/extraction-request.schema.json`.
2. Set `extraction_mode` explicitly for every asset:
   - Use `direct_crop` for rectangular panels, slots, and other assets whose bbox is the desired canvas.
   - Use `foreground_extract` when the asset needs a transparent background.
3. Run:

```powershell
python scripts/extract_assets.py --request path\to\extraction-request.json --output-dir path\to\extraction
```

4. Read `extraction-result.json`. Treat only explicit program failures as `failed`; Stage2-D owns visual quality review.

## Foreground extraction contract

Build a clamped ROI by expanding `final_bbox` with the configured fixed padding. Define the Context Ring as pixels inside that ROI but outside `final_bbox`. Use the ring only to estimate local background; never classify ring pixels as target foreground.

Estimate RGB background with the ring median. If too few ring pixels exist, explicitly fall back to the ROI border median, then the source-image border median. Generate `color_distance_v0` by thresholding Euclidean RGB distance inside the final-bbox core. Apply deterministic Pillow close/open morphology, optional dilation, and Gaussian blur. Multiply generated alpha by source alpha and emit straight-alpha RGBA without changing source RGB.

Write foreground assets to `assets/<asset_id>.png`, binary debug masks to `masks/<asset_id>_mask.png`, and traceable parameters and coordinates to `extraction-result.json`.

## Direct crop contract

Crop exactly `final_bbox`, preserve source RGB and alpha, and skip ROI padding, background estimation, mask creation, morphology, and alpha generation.

## Boundaries

Never:

- alter or replace `final_bbox`;
- discover, split, classify, deduplicate, or retry assets;
- use generated pixels, inpainting, redraw, alpha matting, halo repair, or edge decontamination;
- emit `review_required`, quality scores, or Stage2-C normalization.

If alpha bounds are inspected downstream, record them as extraction metadata only. Do not write them back as a new bbox.

