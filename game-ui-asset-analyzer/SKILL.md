---
name: game-ui-asset-analyzer
description: Prepare a deterministic width-capped analysis image from a game UI screenshot, analyze it into structured asset candidates, map candidate bounding boxes back to original-image pixels, and optionally generate deterministic local bbox-refinement suggestions for direct-crop icons. Use for Stage2 v0.1 UI asset analysis and icon bbox QA with reproducible coordinate spaces; do not use for image extraction, segmentation models, alpha matting, repair, redraw, engine assembly, or final PNG manifests.
---

# Game UI Asset Analyzer

Analyze visible UI elements in a deterministic analysis image while leaving resizing, metadata, coordinate mapping, IDs, and validation to the bundled Python scripts.

## Workflow

1. Receive the original source image, such as `input/preview.png`.
2. Run `scripts/prepare_analysis_input.py` with the source image. Create `analysis/analysis-input.png` at a maximum width of 1024 pixels without upscaling, cropping, or padding, and create `analysis/analysis-input-meta.json` from real image dimensions.
3. Inspect only `analysis-input.png`. Treat its top-left corner as `(0, 0)`, with `x` increasing rightward and `y` downward. Record integer analysis-image pixel bounds only as `x`, `y`, `width`, and `height`.
4. Read [references/asset-taxonomy.md](references/asset-taxonomy.md) before classifying candidates. Use exactly one frozen `semantic_type`; do not add categories.
5. Read [references/extraction-strategies.md](references/extraction-strategies.md) before deciding `should_extract`, `strategy`, and `issues`. Use only the documented enums and consistency rules.
6. If validated Stage1 structure is available, set `source_ref` only when a candidate has a reliable relationship to an existing Stage1 component ID. Otherwise omit it or set it to `null`; never invent an ID.
7. Produce only a JSON array conforming to `schemas/asset-candidates.schema.json`. Each candidate must contain `label`, `semantic_type`, `bbox`, `should_extract`, `strategy`, `issues`, and `reason`; `source_ref` is optional. Every candidate bbox is in `analysis-input.png` pixels.
8. Run `scripts/build_asset_analysis.py` with the original `--source-image`, prepared `--analysis-image`, and candidate JSON. Let the script read both real sizes, validate candidates against the analysis image, map bbox edges into source-image pixels, clamp source bounds, sort candidates, generate IDs, and write `asset-analysis.json`.
9. Run `scripts/validate_asset_analysis.py` on the result with the original source image. Every final asset bbox is in original source-image pixels.
10. Optionally run `scripts/bbox_refiner.py` after validation. Refine only assets where `should_extract` is `true`, `strategy` is `direct_crop`, and `semantic_type` is `icon`. Write `bbox-refinement.json` as a separate QA/downstream suggestion; never overwrite `asset-analysis.json`.

Do not ask the vision model to read `source_size`, calculate scale factors, or convert a bbox back to source coordinates. Do not ask it to emit `id`, `schema_version`, `source_image`, `source_size`, or `taxonomy_version`. The model is responsible only for `label`, `semantic_type`, `bbox` in analysis-image pixels, `should_extract`, `strategy`, `issues`, `reason`, and optional `source_ref`. Do not use `reason` for machine decisions; it exists only for human debugging and QA.

## Recommended run layout

```text
runs/<run-id>/
|-- input/
|   `-- preview.png
`-- analysis/
    |-- analysis-input.png
    |-- analysis-input-meta.json
    |-- asset-candidates.json
    |-- asset-analysis.json
    |-- bbox-refinement.json
    `-- debug-refiner/
```

Keep the artifacts distinct: `preview.png` is the original; `analysis-input.png` is the deterministic model input; candidate bboxes use analysis-image pixels; final analysis bboxes use original-image pixels; refinement output contains optional local pixel-analysis suggestions in original-image pixels.

## Optional bbox refinement

Run the refiner only after producing and validating `asset-analysis.json`:

```powershell
python scripts/bbox_refiner.py --source-image path\to\preview.png --asset-analysis path\to\asset-analysis.json --output path\to\bbox-refinement.json --debug-dir path\to\debug-refiner
```

Use `--ids icon_001,icon_002` only when callers provide verified existing IDs. The refiner estimates local border background, creates an adaptive color-distance mask inside an expanded ROI, analyzes 8-connected components, groups nearby icon fragments, and applies small safety padding. A final conservative acceptance gate rejects excessive area, center, or dimension changes: accepted results use `status: success` and `use_bbox: refined`; rejected results use `status: fallback` and `use_bbox: coarse`. The vision model does not participate in refinement and must not generate confidence values.

## Responsibility boundary

Perform only:

- visual observation of the UI image;
- taxonomy classification;
- pixel bbox estimation;
- `should_extract` and strategy decisions;
- structured issue tagging;
- optional Stage1 `source_ref` association;
- candidate JSON output;
- optional deterministic local bbox refinement for eligible direct-crop icons.

Do not perform:

- PNG cropping or final asset manifest creation;
- automatic replacement of formal bboxes or asset-granularity reconstruction;
- bbox refinement for panels, buttons, illustrations, frames, decorations, progress bars, backgrounds, text, or unknown elements;
- background removal, SAM segmentation, masking, or inpainting;
- repair of occluded assets, text removal, or asset redraw;
- FairyGUI XML or other engine output;
- Stage3 assembly;
- calls to external image APIs or extraction pipelines.

If a requested operation crosses this boundary, return the validated analysis contract only and state that extraction or assembly belongs to a later stage.
