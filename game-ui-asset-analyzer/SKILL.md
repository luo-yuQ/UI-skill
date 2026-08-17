---
name: game-ui-asset-analyzer
description: Classify recursive UI nodes with the Stage2-A Node Router v0.1 contract, split structural groups into stable direct regions, expand repeated groups into peer component instances, decompose selected components into reusable visual asset candidates, assign the v0.3 four-state extraction strategy contract, map candidate bounding boxes back to original-image pixels, and optionally generate deterministic local bbox-refinement suggestions for direct-crop icons. Use for Stage2-A node routing, structural splitting, repeated-instance expansion, UI asset decomposition, strategy analysis, and icon bbox QA with reproducible coordinate spaces; do not use for recursive traversal, image extraction, segmentation models, alpha matting, pixel generation, engine assembly, or final PNG manifests.
---

# Game UI Asset Analyzer

Analyze reusable visual assets in a deterministic analysis image while leaving resizing, metadata, coordinate mapping, IDs, and validation to the bundled Python scripts. Target visual asset boundaries, not business-function regions, interaction hit areas, or whole interactive components.

## Workflow

For one Current Node, use [references/node-router-v0.1.md](references/node-router-v0.1.md) with the existing deterministic Analysis Image, emit only `node_role`, diagnostic `confidence`, and `reason` matching `schemas/node-route.schema.json`, and run `scripts/validate_node_route.py`. Let `ROLE_ACTION_MAP` resolve `next_action`; never ask the VLM to emit it. Do not execute the action or traverse the tree.

When the resolved action is `structural_split`, use [references/structural-split-v0.1.md](references/structural-split-v0.1.md), emit `schemas/structural-split.schema.json`, and run `scripts/validate_structural_split.py` against the actual Analysis Image. Keep repeated collections whole, emit only direct structural children, and optionally create a human-review overlay with `scripts/render_structural_overlay.py`. Do not feed the overlay back into the model or change the result from it.

When the resolved action is `expand_instances`, use [references/expand-instances-v0.1.md](references/expand-instances-v0.1.md), emit `schemas/expand-instances.schema.json`, and run `scripts/validate_expand_instances.py` against the actual Analysis Image. Emit only peer component instances of the repeated template, keep instance internals intact, and optionally create a human-review overlay with `scripts/render_instances_overlay.py`. Do not feed the overlay back into the model or change the result from it.

When the resolved action is `semantic_decompose`, use [references/semantic-decompose-v0.1.md](references/semantic-decompose-v0.1.md), emit `schemas/semantic-decomposition.schema.json`, and run `scripts/validate_semantic_decomposition.py` against the actual Analysis Image. That frozen scoped contract identifies direct visual-asset children only; do not change its taxonomy or behavior.

Current recursive Stage2-A status: Level-1 Region Decomposition v0.1, Coordinate Contract v0.1, `semantic_decompose` v0.1, `structural_split` v0.1, and `expand_instances` v0.1 are **FROZEN**; Node Router v0.1 is **contract implemented / behavior validated**; the recursive engine is **not implemented**.

The workflow below remains the existing full-screen flat asset-analysis workflow and is unchanged by the node-scoped contract.

1. Receive the original source image, such as `input/preview.png`.
2. Run `scripts/prepare_analysis_input.py` with the source image. Create `analysis/analysis-input.png` at a maximum width of 1024 pixels without upscaling, cropping, or padding, and create `analysis/analysis-input-meta.json` from real image dimensions.
3. Inspect only `analysis-input.png`. Treat its top-left corner as `(0, 0)`, with `x` increasing rightward and `y` downward. Record integer analysis-image pixel bounds only as `x`, `y`, `width`, and `height`.
4. Complete the decomposition and completeness passes below. Do not assign extraction strategy while candidate discovery is still incomplete.
5. Read [references/asset-taxonomy.md](references/asset-taxonomy.md) before classifying candidates. Use exactly one frozen `semantic_type`; do not add categories.
6. Read [references/extraction-strategies.md](references/extraction-strategies.md) only after decomposition, then decide `should_extract`, `strategy`, and `issues` independently for every candidate. Use only the documented enums and consistency rules.
7. If validated Stage1 structure is available, set `source_ref` only when a candidate has a reliable relationship to an existing Stage1 component ID. Otherwise omit it or set it to `null`; never invent an ID.
8. Produce only a flat JSON array conforming to `schemas/asset-candidates.schema.json`. Each candidate must contain `label`, `semantic_type`, `bbox`, `should_extract`, `strategy`, `issues`, and `reason`; `source_ref` is optional. Every candidate bbox is in `analysis-input.png` pixels. Do not add parent/child relationship fields.
9. Run `scripts/build_asset_analysis.py` with the original `--source-image`, prepared `--analysis-image`, and candidate JSON. Let the script read both real sizes, validate candidates against the analysis image, map bbox edges into source-image pixels, clamp source bounds, sort candidates, generate IDs, and write `asset-analysis.json`.
10. Run `scripts/validate_asset_analysis.py` on the result with the original source image. Every final asset bbox is in original source-image pixels.
11. Optionally run `scripts/bbox_refiner.py` after validation. Refine only assets where `should_extract` is `true`, `strategy` is `direct_crop`, and `semantic_type` is `icon`. Write `bbox-refinement.json` as a separate QA/downstream suggestion; never overwrite `asset-analysis.json`.

## Asset Decomposition Granularity Policy

Prefer the **smallest visually complete reusable asset**. Do not subdivide into arbitrary pixels or incidental fragments. Emit a separate candidate when an element has its own visual identity and boundary and could plausibly be reused or cut out independently.

For example, decompose a clickable offer card into candidates such as:

```text
offer card panel / frame
crystal illustration
price button
amount text
bonus text
BEST VALUE decoration
```

Do not emit only `button = whole offer card` merely because the whole card is clickable.

### Continue inside containers

Finding a panel, card-like region, button container, toolbar, bottom bar, navigation area, or other composite interactive region never completes candidate discovery. Scan inside every such container for independent icons, illustrations, frames, decorations, buttons, progress bars, and text.

### Keep reusable parents and independent children

Emit both a reusable parent/container candidate and its independent child asset candidates when both have visual identity. Their bboxes may overlap; overlap does not by itself mean duplication. A card surface, standalone frame, or complete panel surface may remain a parent candidate while its contained assets are also emitted.

Keep the output as the existing flat candidate array. Express the relationship through the observed bboxes and labels only; do not add `parent_id`, `children`, `container_id`, or similar fields.

### Separate interaction from visual boundaries

Apply this principle:

```text
interaction hit area != reusable visual asset boundary
```

Classify a visually independent control surface as `button`. A card shell whose main visual role is to contain and organize several assets is often a `panel`, even if the whole card is clickable; an independent price control or CTA inside it may be a `button`. Do not map clickability to either type mechanically, and never let interactivity erase the semantic identity of contained assets.

## Completeness passes

Perform these lightweight visual passes; do not implement them as a deterministic completeness validator:

1. Identify major regions, containers, and large reusable visual assets.
2. Inspect every container for independent child visual assets.
3. Inspect bottom bars, top bars, corners, repeated icons, badges, and decorations.
4. Check whether any obviously independent visual asset still lacks a candidate.

Only after all four passes, assign `should_extract`, `strategy`, and `issues` to each candidate independently. A parent marked `advanced_required` does not permit omission of its children.

## Strategy Contract v0.3

Use exactly `direct_crop`, `foreground_extract`, `advanced_required`, or `do_not_extract`. Read [references/extraction-strategies.md](references/extraction-strategies.md) for the complete definitions and examples.

Apply this decision order independently to every parent and child candidate after decomposition:

1. If it should not become an image asset, use `do_not_extract`.
2. If the intended reusable asset requires pixels missing from the screenshot, use `advanced_required`.
3. If every pixel in the rectangular crop belongs to the final reusable asset and can be retained unchanged, use `direct_crop`.
4. If all target pixels exist but any pixels inside the bbox must be removed or made transparent, use `foreground_extract`; otherwise use `advanced_required`.

Treat `foreground_extract` only as isolation of a complete foreground from pixels already present through mask/alpha separation. It does not fill, redraw, recover occlusion, remove baked-in target content, or guess missing boundaries. Treat `advanced_required` as a record that the ideal independent asset cannot be reliably obtained from the current screenshot; it is not a repair plan, and Stage2 never generates missing pixels.

Apply `bbox tightness != direct-crop eligibility`. A small icon with a tight bbox still requires `foreground_extract` when its rectangular corners or surrounding pixels contain card, panel, or unrelated UI background that should become transparent. `direct_crop` requires the crop to be reusable pixel-for-pixel, not merely tightly bounded.

Do not derive strategy from `semantic_type` or an issue string. Issues are diagnostic. A complete illustration on a complex background may use `foreground_extract`, while a panel whose surface is covered by children may use `advanced_required`. Parent and child strategies are independent.

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

Use `--ids icon_001,icon_002` only when callers provide verified existing IDs. BBox Refiner v0.2 estimates local border background, creates an adaptive color-distance mask inside an expanded ROI, and analyzes 8-connected components. It generates multiple single-component and progressively merged bbox candidates, scores them by center proximity, coarse overlap, area similarity, and width/height similarity, and strongly penalizes oversized icon candidates. Try candidates in score order against the existing conservative acceptance gate; accept the first passing candidate, otherwise retain coarse. Record `candidate_count` and `selected_candidate_rank` in `bbox-refinement.json`. The vision model does not participate in refinement and must not generate scores or confidence values.

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
