# Game UI Asset Analyzer v0.1

This Stage2 skill prepares a deterministic visual-analysis image, then maps visual-model candidate bboxes back into the original source image while building `asset-analysis.json`. It does not extract assets or refine bboxes.

## Requirements

- Python 3.10+
- Pillow
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

## Recommended run layout

```text
runs/<run-id>/
|-- input/
|   `-- preview.png
`-- analysis/
    |-- analysis-input.png
    |-- analysis-input-meta.json
    |-- asset-candidates.json
    `-- asset-analysis.json
```

`asset-candidates.json` uses analysis-image pixels. `asset-analysis.json` uses original `preview.png` pixels.

## Test

```powershell
python -m pytest -q
```
