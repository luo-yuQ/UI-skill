# Game UI Asset Analyzer v0.1

This Stage2 skill turns visual-model asset candidates into a deterministic `asset-analysis.json`. It does not crop or edit images.

## Requirements

- Python 3.10+
- Pillow
- jsonschema

## Minimal usage

Create `asset-candidates.json` as a JSON array matching `schemas/asset-candidates.schema.json`, then run:

```powershell
python scripts/build_asset_analysis.py --source-image path\to\preview.png --model-output path\to\asset-candidates.json --output path\to\asset-analysis.json
python scripts/validate_asset_analysis.py path\to\asset-analysis.json --source-image path\to\preview.png
```

The builder reads the actual image size, validates every bbox, sorts candidates by `y`, `x`, `width`, then `height`, preserves input order for exact bbox ties, and assigns per-type IDs such as `button_001` and `icon_001`.

See `examples/asset-candidates.json` for a minimal candidate document. The final document always contains runtime-generated `schema_version`, `source_image`, `source_size`, `taxonomy_version`, and asset IDs.

## Test

```powershell
python -m unittest discover -s tests -v
```
