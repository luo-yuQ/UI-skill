# Game UI Asset Extractor v0.1

Deterministic Stage2-B extraction for assets whose original-image `final_bbox` is already frozen by Stage2-A.

## Requirements

- Python 3.10+
- Pillow
- NumPy
- jsonschema

The default image backend is `pillow`. Installing OpenCV does not change results.

### Optional: SAM backend (`sam1_vit_b`, frozen v0.1)

- torch
- segment-anything (SAM1, `model type = vit_b` only)
- a SAM ViT-B checkpoint supplied explicitly per run

SAM dependencies and checkpoints are never auto-installed or auto-downloaded. Checkpoints stay out of Git; pass them per run via `--sam-checkpoint` or `config.sam_checkpoint`.

## Backends

| backend | id | scope |
| --- | --- | --- |
| Legacy local-background foreground extraction (unchanged) | `pillow` | `direct_crop` + `foreground_extract` |
| SAM1 ViT-B box-only segmentation (frozen v0.1) | `sam1_vit_b` | `direct_crop` (passthrough) + `foreground_extract` |

## SAM v0.1 baseline (frozen)

The `sam1_vit_b` backend implements exactly the verified PoC baseline:

- **Model**: SAM1 ViT-B (`vit_b`). Checkpoint must be provided explicitly (`--sam-checkpoint` / `config.sam_checkpoint`); device is `auto` (CUDA when available, otherwise CPU with a recorded fallback), `cuda`, or `cpu`.
- **Authoritative input**: the full source image plus the reviewed `final_bbox` in source-image pixel coordinates. Tight crops are never used as the SAM image.
- **Encode once**: `predictor.set_image(full_source)` runs exactly once per request; every asset is prompted against that single encoding.
- **Prompt**: box only (`predictor.predict(box=..., multimask_output=True)`). No point coordinates or labels are passed.

  Point prompting: NOT part of v0.1 baseline. Reserved for future fallback.
- **Candidate selection**: winner = max SAM score among multimask candidates.

  Geometry-aware candidate scoring (sam_score + containment + mask-bbox IoU): evaluated but not adopted in v0.1 — offline comparison over 28 valid assets produced identical winners.
- **Mask postprocess** (deterministic):
  1. 3x3 full-ones binary close, 1 iteration (dilate 1px then erode 1px);
  2. 8-connected component filtering: keep every component containing a positive point (always empty in v0.1 — there are no points), otherwise keep the largest component, plus every component with area >= 8% of the largest component.
- **RGBA output**: RGB comes from the untouched source pixels; alpha = postprocessed binary mask * source alpha (opaque when the source has no alpha). No matting, halo repair, or occlusion completion.

  Occlusion / clean-base repair: belongs to Stage2-C and is out of scope.
- **Diagnostics**: every SAM asset records model, prompt, candidate list, `winner_index`, `winner_sam_score`, postprocess parameters, mask area, and component counts in `mask_parameters`.

These are frozen implementation contracts, not model-quality guarantees.

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

For the SAM backend set `"backend": "sam1_vit_b"` and add `sam_model_type` (`"vit_b"`), `sam_checkpoint`, and `device` (`"auto"` / `"cuda"` / `"cpu"`). Pillow-only config keys remain accepted but are inert under the SAM backend.

`final_bbox` uses the existing Stage2-A `{x, y, width, height}` contract in original source-image pixels. Stage2-B validates but never clamps or rewrites it.

## Run

Legacy pillow backend (unchanged):

```powershell
python scripts/extract_assets.py --request path\to\extraction-request.json --output-dir path\to\extraction
```

SAM backend:

```powershell
python scripts/extract_assets.py --request path\to\extraction-request.json --output-dir path\to\extraction --backend sam1_vit_b --sam-checkpoint path\to\sam_vit_b_01ec64.pth
```

`--sam-checkpoint`, `--sam-model-type`, and `--device` override the request config; the checkpoint path is required either way and is never hardcoded.

Output:

```text
extraction/
|-- assets/
|   `-- icon_001.png
|-- masks/
|   `-- icon_001_mask.png
`-- extraction-result.json
```

`direct_crop` writes an exact RGBA crop and no mask. `foreground_extract` expands a clamped ROI, estimates local background from the Context Ring, creates a binary color-distance mask inside the frozen bbox core, applies close/open and soft-alpha processing, and writes both RGBA and binary mask PNGs. Under the SAM backend, `foreground_extract` instead runs the frozen SAM v0.1 chain and writes the ROI crop of the postprocessed mask as the binary mask.

Relative `source_image` paths are resolved from the request file directory. Output paths in metadata are relative to the extraction output directory.

## Error handling

Failures are explicit and diagnosable; there is no silent fallback to another segmentation algorithm. SAM-specific errors include: missing `segment-anything`/torch, missing or unloadable checkpoint, invalid device, out-of-bounds or non-positive bboxes, empty SAM masks, and abnormal candidate counts. Per-asset failures are recorded as `failed` assets with `failure_reason`; predictor/load failures abort the whole request.


