# Game UI Asset Analyzer — Stage2-A Strategy Contract v0.3

This Stage2-A skill decomposes a UI into reusable visual assets, assigns the four-state v0.3 extraction strategy contract, prepares a deterministic visual-analysis image, maps visual-model candidate bboxes back into the original source image while building `asset-analysis.json`, and optionally writes standalone bbox-refinement suggestions for eligible icons. It targets visual asset boundaries rather than interaction hit areas. It does not extract assets or overwrite formal bboxes.

## Recursive Stage2-A status

- Level-1 Region Decomposition v0.1 — **FROZEN**
- Coordinate Contract v0.1 — **FROZEN**
- `semantic_decompose` v0.1 — **FROZEN**
- Node Router v0.1 — **FROZEN**
- `structural_split` v0.1 — **FROZEN**
- `expand_instances` v0.1 — **FROZEN**
- Asset / Stop Contract v0.1 — **FROZEN**
- Recursive Runtime — **not implemented**

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

See `examples/asset-candidates.json` for a compound-card candidate document with an overlapping reusable parent, independent children, and all four strategies: `direct_crop`, `foreground_extract`, `advanced_required`, and `do_not_extract`. The final document always contains runtime-generated `schema_version`, `source_image`, `source_size`, `taxonomy_version`, and asset IDs. Strategy Contract v0.3 extends the v0.1 schema enum without changing its fields or the taxonomy enum.

## Recursive Stage2-A: Node Router v0.1

Use the production prompt and classification contract in `references/node-router-v0.1.md` on one deterministic Current Node Analysis Image. Validate the raw VLM JSON with `schemas/node-route.schema.json`, then let engineering code resolve the frozen role-to-action mapping:

```powershell
python scripts/validate_node_route.py path\to\node-route.json
```

The VLM returns only `node_role`, diagnostic `confidence`, and a non-empty `reason`. It never emits `next_action`; `scripts/validate_node_route.py` owns the deterministic mapping and rejects unknown roles. It does not execute `structural_split`, `expand_instances`, `semantic_decompose`, or tree recursion.

## Recursive Stage2-A: `structural_split` v0.1

For an already-selected `structural_group`, use the production prompt and behavior contract in `references/structural-split-v0.1.md`. It emits only stable Direct Children, preserves a repeated collection as one structural child, and supports `no_useful_structural_split: true` instead of manufacturing an ineffective near-parent-sized child.

Prepare the shared deterministic 1024-pixel-wide Node Crop Analysis Image, then validate the immutable VLM output against that real image:

```powershell
python scripts/prepare_analysis_input.py --source-image path\to\node-crop.png --output-image path\to\analysis-image.png --metadata-output path\to\analysis-image-meta.json --max-width 1024 --force-width
python scripts/validate_structural_split.py path\to\structural-split.json --analysis-image path\to\analysis-image.png
```

For optional human review, render the validated bboxes directly in Analysis Image coordinates:

```powershell
python scripts/render_structural_overlay.py --analysis-image path\to\analysis-image.png --structural-split path\to\structural-split.json --output-image path\to\structural-overlay.png
```

The validator checks only schema, decision consistency, required fields, unique child IDs, numeric ranges, and real-image bbox bounds. The overlay does not modify JSON, call a VLM, perform semantic review, or feed a correction loop. Child nodes are intended to re-enter the Router only when a future orchestration layer exists; recursive traversal is not implemented.

## Recursive Stage2-A: `expand_instances` v0.1

For an already-selected `repeated_group`, use the production prompt and behavior contract in `references/expand-instances-v0.1.md`. It emits only Direct Child Instances of one shared component template, keeps instance internals intact, and records visibility limits with `partial_instance`.

Reuse the shared deterministic 1024-pixel-wide Node Crop Analysis Image preparation, then validate the immutable VLM output against that real image:

```powershell
python scripts/prepare_analysis_input.py --source-image path\to\node-crop.png --output-image path\to\analysis-image.png --metadata-output path\to\analysis-image-meta.json --max-width 1024 --force-width
python scripts/validate_expand_instances.py path\to\instances.json --analysis-image path\to\analysis-image.png
```

For optional human review, render instance IDs, the shared `instance_type`, and partial markers directly in Analysis Image coordinates:

```powershell
python scripts/render_instances_overlay.py --analysis-image path\to\analysis-image.png --instances path\to\instances.json --output-image path\to\instances-overlay.png
```

The validator checks only schema, exact `repeat_count`, required fields, unique instance IDs, numeric ranges, `partial_instance`, and real-image bbox bounds. The renderer reuses the existing deterministic overlay layout utilities; it does not modify JSON, call a VLM, perform semantic review, or feed a correction loop. Instances may re-enter the Router only when a future orchestration layer exists; recursive traversal is not implemented.

## Recursive Stage2-A: `semantic_decompose` v0.1

For an already-selected `component` or `component_instance`, use the production prompt and behavior contract in `references/semantic-decompose-v0.1.md`. Its VLM JSON must conform to `schemas/semantic-decomposition.schema.json`; all child bboxes are complete visible extents in the deterministic 1024-pixel-wide Analysis Image coordinate space.

Validate the immutable VLM output against the actual Analysis Image:

```powershell
python scripts/validate_semantic_decomposition.py path\to\semantic-decomposition.json --analysis-image path\to\analysis-image.png
```

This validator checks only schema, enums, decision consistency, required fields, unique child IDs, declared/actual Analysis Image size, confidence, and bbox bounds. It never calls a VLM, changes a bbox, rejects overlap, or judges baked-in text, coherent artwork, or frame semantics. Coordinate preparation and Analysis Image-to-Node Crop mapping remain in the existing Coordinate Contract implementation.

## Recursive Stage2-A: Asset / Stop Contract v0.1

Use the deterministic contract in `references/asset-stop-contract-v0.1.md` to decide whether a node is terminal. It adds no stop prompt or VLM call. The resolver reuses the existing Router role-to-action mapping and reads the frozen taxonomy from the semantic-decomposition schema:

```powershell
python scripts/resolve_terminal_state.py --node-role asset
python scripts/resolve_terminal_state.py --produced-by semantic_decompose --taxonomy illustration
python scripts/resolve_terminal_state.py --produced-by expand_instances
python scripts/resolve_terminal_state.py --produced-by structural_split
```

Outputs conform to `schemas/asset-stop-result.schema.json`. Valid semantic-decomposition children stop directly, expanded instances continue directly to semantic decomposition, and unclassified structural children return `requires_router: true`. Conflicting provenance and roles fail explicitly. The resolver does not read images, execute actions, traverse nodes, or implement Recursive Runtime.

Deferred: `retain_composite` and composite asset retention policy.

## Optional icon bbox refinement

After validating `asset-analysis.json`, run:

```powershell
python scripts/bbox_refiner.py --source-image path\to\preview.png --asset-analysis path\to\asset-analysis.json --output path\to\bbox-refinement.json --debug-dir path\to\debug-refiner
```

Optional controls:

- `--expand-px 12`: override the default `max(8, round(max(width, height) * 0.45))` ROI expansion.
- `--safety-padding 2`: set final padding around detected foreground.
- `--ids icon_001,icon_002`: process only verified IDs.

BBox Refiner v0.2 processes only `should_extract=true`, `strategy=direct_crop`, `semantic_type=icon`. It keeps the v0.1 foreground detector and 8-connected components, but generates multiple single-component and progressively merged bbox candidates. Deterministic scoring considers center distance, coarse overlap, area similarity, and width/height similarity, with a strong oversize penalty. Ranked candidates pass through the unchanged acceptance gate—area ratio `0.6–1.5`, center shift at most `10px`, and width/height ratios `0.6–1.5`—until one succeeds. Output records `candidate_count` and the accepted `selected_candidate_rank`; all rejected candidates produce `status=fallback`, `use_bbox=coarse`, and a null rank. Results go to `bbox-refinement.json`; `asset-analysis.json` is read-only.

With `--debug-dir`, every processed eligible asset writes `<id>-roi.png`, `<id>-mask.png`, `<id>-overlay.png`, and `<id>-candidates.png`. The candidate overlay shows the coarse bbox, ROI, top three ranked candidates, and the final accepted bbox when present.

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
