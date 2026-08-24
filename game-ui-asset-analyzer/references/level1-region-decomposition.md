# Stage2-A Level-1 Region Decomposition v0.1

This is the visual-model contract for the independent Level-1 intermediate artifact. It precedes asset analysis and does not change the frozen Stage2-A asset taxonomy or strategy contract.

## Responsibility split

The VLM identifies a small, image-dependent set of major logical visual regions in the complete source screenshot. A region occupies a meaningful page area and can still organize multiple child contents. The VLM returns only the raw JSON defined by `schemas/level1-regions.schema.json`.

Deterministic code validates source-image pixel bounds, adds context padding, clamps that padding to the image, crops each ROI, optionally upscales small ROIs, writes coordinate transforms, and creates a QA overlay. Engineering logic uses `id` and geometry only. It must never branch on `label`.

## VLM instructions

- Inspect the complete UI screenshot.
- Identify the page's major, relatively independent visual-content regions. The number is dynamic and is determined by the image.
- Assign stable engineering IDs in document order: `region_001`, `region_002`, and so on. IDs must be unique.
- Give each region a short image-specific human-readable `label`; `description` and `confidence` are optional.
- Return each `bbox` in original-image integer pixels using top-left `x`, `y`, `width`, and `height`. Width and height must be positive and the box must remain inside the source image.
- Cover each major region completely enough that later child analysis retains its context.
- Small overlaps are allowed when complete context requires them. Do not deduplicate regions merely because their boxes overlap.
- Register the global background only as `background_root` with `requires_reconstruction: true`. Do not emit the global background as a normal region.

## Do not do at Level 1

- Do not identify individual buttons, icons, text, cards, decorations, or standalone artwork.
- Do not force Header, Footer, or any other named region to exist.
- Do not force a fixed number of regions or require regions to be non-overlapping.
- Do not use `region` as an eleventh asset semantic type and do not emit `semantic_type`.
- Do not enter Level 2 or produce masks, foreground extraction, background reconstruction, OCR, or asset crops.

## Stop condition

Stop when the result covers the page's principal content-organization regions and any further split would enter card, button, icon, text, or similarly fine-grained content. Do not continue into Level 2.

## Raw output example

```json
{
  "schema_version": "0.1",
  "source_image": "preview.png",
  "source_size": {"width": 1248, "height": 832},
  "background_root": {
    "id": "background_root",
    "node_kind": "background_root",
    "requires_reconstruction": true
  },
  "regions": [
    {
      "id": "region_001",
      "node_kind": "region",
      "label": "Header",
      "description": "Top navigation and currency status area",
      "bbox": {"x": 0, "y": 0, "width": 1248, "height": 70},
      "confidence": 0.95
    }
  ]
}
```

`level1-regions.raw.json` is immutable VLM evidence. `process_level1_regions.py` writes the separate `level1-regions.json`, crop PNGs, and overlay. It never treats a full-image crop as a reconstructed background.
