# Game UI Asset Analyzer v0.1

This Stage2 skill prepares a deterministic visual-analysis image, maps visual-model candidate bboxes back into the original source image while building `asset-analysis.json`, and optionally writes standalone bbox-refinement suggestions for eligible icons. It does not extract assets or overwrite formal bboxes.

## Requirements

- Python 3.10+
- Pillow
- NumPy
- jsonschema

## Minimal usage

Prepare the model input first:

```powershell
python scripts/prepare_analysis_input.py --source-image path\to\preview.png --output-image path\to\analysis-input.png --metadata-output path\to\analysis-input-meta.json --max-width 1024
```

Have the visual model analyze only `analysis-input.png`. Create `asset-candidates.json` as a JSON array matching `schemas/asset-candidates.schema.json`; every candidate bbox uses `analysis-input.png` pixels. Then run:

```powershell
python scripts/build_asset_analysis.py --source-image path\to\preview.png --analysis-image path\to\analysis-input.png --model-output path\to\asset-candidates.json --output path\to\asset-analysis.json
python scripts/validate_asset_analysis.py path\to\asset-analysis.json --source-image path\to\preview.png
```

The preparer reads the real source dimensions, caps width at 1024 without upscaling, uses proportional rounded height and LANCZOS resampling, writes PNG, and records both real sizes and per-axis source scale factors. It never crops or pads.

The builder reads both image files rather than trusting JSON metadata. It validates candidates against the analysis-image bounds, maps all four bbox edges with independent actual `x` and `y` scale factors, clamps to source bounds, and emits source-image bboxes. It then sorts by final `y`, `x`, `width`, and `height`, preserves input order for exact ties, and assigns per-type IDs such as `button_001` and `icon_001`.

For compatibility, omit `--analysis-image` to treat the source image as the analysis image and leave bbox coordinates unchanged.

See `examples/asset-candidates.json` for a minimal candidate document. The final document always contains runtime-generated `schema_version`, `source_image`, `source_size`, `taxonomy_version`, and asset IDs.

## Optional icon bbox refinement

After validating `asset-analysis.json`, run:

```powershell
python scripts/bbox_refiner.py --source-image path\to\preview.png --asset-analysis path\to\asset-analysis.json --output path\to\bbox-refinement.json --debug-dir path\to\debug-refiner
```

Optional controls:

- `--expand-px 12`: override the default `max(8, round(max(width, height) * 0.45))` ROI expansion.
- `--safety-padding 2`: set final padding around detected foreground.
- `--ids icon_001,icon_002`: process only verified IDs.

v0.1 processes only `should_extract=true`, `strategy=direct_crop`, `semantic_type=icon`. It estimates background from ROI border pixels, uses adaptive RGB color distance, finds 8-connected components, chooses the component group nearest and overlapping the coarse bbox, and applies deterministic reasonability checks. A final acceptance gate requires area ratio `0.6–1.5`, center shift at most `10px`, and width/height ratios `0.6–1.5`. Accepted results set `use_bbox=refined`; rejected results set `status=fallback` and `use_bbox=coarse`. Results go to `bbox-refinement.json`; `asset-analysis.json` is read-only.

With `--debug-dir`, every processed eligible asset writes `<id>-roi.png`, `<id>-mask.png`, and `<id>-overlay.png`. The overlay uses yellow for coarse bbox, blue for ROI, and green for successful refined bbox.

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

`asset-candidates.json` uses analysis-image pixels. `asset-analysis.json` uses original `preview.png` pixels.

## Test

```powershell
python -m pytest -q
```
