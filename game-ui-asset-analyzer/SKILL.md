---
name: game-ui-asset-analyzer
description: Analyze a game UI screenshot into structured asset candidates with semantic types, pixel bounding boxes, extraction decisions, strategies, issues, and optional Stage1 source references, then deterministically build asset-analysis.json. Use for Stage2 v0.1 asset analysis of UI screenshots; do not use for image extraction, segmentation, repair, redraw, engine assembly, or final PNG manifests.
---

# Game UI Asset Analyzer

Analyze visible UI elements while leaving deterministic metadata and validation to the bundled Python scripts.

## Workflow

1. Inspect the complete source image. Treat the top-left corner as `(0, 0)`, with `x` increasing rightward and `y` downward. Record integer pixel bounds only as `x`, `y`, `width`, and `height`.
2. Read [references/asset-taxonomy.md](references/asset-taxonomy.md) before classifying candidates. Use exactly one frozen `semantic_type`; do not add categories.
3. Read [references/extraction-strategies.md](references/extraction-strategies.md) before deciding `should_extract`, `strategy`, and `issues`. Use only the documented enums and consistency rules.
4. If validated Stage1 structure is available, set `source_ref` only when a candidate has a reliable relationship to an existing Stage1 component ID. Otherwise omit it or set it to `null`; never invent an ID.
5. Produce only a JSON array conforming to `schemas/asset-candidates.schema.json`. Each candidate must contain `label`, `semantic_type`, `bbox`, `should_extract`, `strategy`, `issues`, and `reason`; `source_ref` is optional.
6. Run `scripts/build_asset_analysis.py` with the source image and candidate JSON. Let the script read image dimensions, validate bounds and enum consistency, sort candidates, generate IDs, and write `asset-analysis.json`.
7. Run `scripts/validate_asset_analysis.py` on the result, passing the source image when available.

Do not ask the vision model to emit `id`, `schema_version`, `source_image`, `source_size`, or `taxonomy_version`. Do not use `reason` for machine decisions; it exists only for human debugging and QA.

## Responsibility boundary

Perform only:

- visual observation of the UI image;
- taxonomy classification;
- pixel bbox estimation;
- `should_extract` and strategy decisions;
- structured issue tagging;
- optional Stage1 `source_ref` association;
- candidate JSON output.

Do not perform:

- PNG cropping or final asset manifest creation;
- background removal, SAM segmentation, masking, or inpainting;
- repair of occluded assets, text removal, or asset redraw;
- FairyGUI XML or other engine output;
- Stage3 assembly;
- calls to external image APIs or extraction pipelines.

If a requested operation crosses this boundary, return the validated analysis contract only and state that extraction or assembly belongs to a later stage.
