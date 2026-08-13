# Game UI Asset Extractor v0.1

Deterministic Stage2-B extraction for assets whose original-image `final_bbox` is already frozen by Stage2-A.

## Requirements

- Python 3.10+
- Pillow
- NumPy
- jsonschema

The v0.1 image backend is always Pillow. Installing OpenCV does not change results.

## Request

```json
{
  "schema_version": "0.1",
  "source_image": "../input/preview.png",
  "config": {
    "backend": "pillow",
    "roi_padding": 10,
    "background_min_pixels": 16,
    "mask_threshold": 22.0,
    "morphology_radius": 1,
    "alpha_dilation_radius": 1,
    "alpha_blur_radius": 1.0
  },
  "assets": [
    {
      "asset_id": "icon_001",
      "asset_type": "icon",
      "final_bbox": {"x": 24, "y": 18, "width": 32, "height": 32},
      "extraction_mode": "foreground_extract"
    }
  ]
}
```

`final_bbox` uses the existing Stage2-A `{x, y, width, height}` contract in original source-image pixels. Stage2-B validates but never clamps or rewrites it.

## Run

```powershell
python scripts/extract_assets.py --request path\to\extraction-request.json --output-dir path\to\extraction
```

Output:

```text
extraction/
|-- assets/
|   `-- icon_001.png
|-- masks/
|   `-- icon_001_mask.png
`-- extraction-result.json
```

`direct_crop` writes an exact RGBA crop and no mask. `foreground_extract` expands a clamped ROI, estimates local background from the Context Ring, creates a binary color-distance mask inside the frozen bbox core, applies close/open and soft-alpha processing, and writes both RGBA and binary mask PNGs.

Relative `source_image` paths are resolved from the request file directory. Output paths in metadata are relative to the extraction output directory.

