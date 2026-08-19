# Game UI Asset Analyzer — Stage2-A Strategy Contract v0.3

This Stage2-A skill decomposes a UI into reusable visual assets, assigns the four-state v0.3 extraction strategy contract, prepares a deterministic visual-analysis image, maps visual-model candidate bboxes back into the original source image while building `asset-analysis.json`, and optionally writes standalone bbox-refinement suggestions for eligible icons. It targets visual asset boundaries rather than interaction hit areas. It does not extract assets or overwrite formal bboxes.

## Recursive Stage2-A status

- Level-1 Region Decomposition v0.1 — **FROZEN**
- Coordinate Contract v0.1 — **FROZEN**
- `semantic_decompose` v0.1.1 — **IMPLEMENTED / COMPONENT-COMPOSITION BOUNDARY PATCH**
- Node Router v0.1 — **FROZEN**
- `structural_split` v0.1 — **FROZEN**
- `expand_instances` v0.1 — **FROZEN**
- Asset / Stop Contract v0.1 — **FROZEN**
- Recursive Runtime v0.1 — **IMPLEMENTED / mechanics validated**
- Production Visual Adapter — **IMPLEMENTED**
- Responses API VLM Client — **IMPLEMENTED / real API smoke test pending**
- Real-image R5 — **PENDING**

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

The validator checks only schema, decision consistency, required fields, unique child IDs, numeric ranges, and real-image bbox bounds. The overlay does not modify JSON, call a VLM, perform semantic review, or feed a correction loop. Recursive Runtime v0.1 sends these unclassified Direct Children to the next level and then back through the Router.

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

The validator checks only schema, exact `repeat_count`, required fields, unique instance IDs, numeric ranges, `partial_instance`, and real-image bbox bounds. The renderer reuses the existing deterministic overlay layout utilities; it does not modify JSON, call a VLM, perform semantic review, or feed a correction loop. Recursive Runtime v0.1 uses provenance to schedule selected instances directly for `semantic_decompose`, without another Router call.

## Recursive Stage2-A: `semantic_decompose` v0.1.1

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

Outputs conform to `schemas/asset-stop-result.schema.json`. Valid semantic-decomposition children stop directly, expanded instances continue directly to semantic decomposition, and unclassified structural children return `requires_router: true`. Conflicting provenance and roles fail explicitly. The resolver itself remains traversal-free; Recursive Runtime v0.1 calls it as a frozen deterministic dependency.

Deferred: `retain_composite` and composite asset retention policy.

## Recursive Stage2-A: Recursive Runtime v0.1

`scripts/recursive_runtime.py` implements the single-process, level-by-level runtime. `RecursiveRuntime.create(...)` initializes the root Node Crop and deterministic Analysis Image; `run()` consumes `current_level_queue`, places all non-terminal children in `next_level_queue`, and advances only after the current level is exhausted. The runtime persists `run-manifest.json`, `runtime-state.json`, the complete `tree.json`, and per-node metadata under `nodes/`.

The Runtime Core requires injected `RouterAdapter`, `StructuralSplitAdapter`, `ExpandInstancesAdapter`, and `SemanticDecomposeAdapter` implementations; it does not require any specific production VLM provider. An adapter may be backed by a production provider, TRAE integration, a fake, a fixture, or another contract-compatible implementation. `scripts/fake_runtime_adapters.py` supplies deterministic fixtures for tests. If an action has no injected adapter, only that Node fails with `adapter_unavailable` under the existing error model; the absence of production provider wiring does not make the Runtime globally blocked. Every adapter result is validated by the existing frozen validator before it may affect the tree.

The production model-call path is `Stage2-A Workflow -> RecursiveRuntime -> Visual Adapter Boundary -> ProductionVisualAdapter -> ResponsesAPIVLMClient -> POST /v1/responses`. Router remains a Workflow capability alongside the three strategies; `ProductionVisualAdapter` is their shared implementation layer, not a fifth peer strategy. `scripts/production_visual_adapter.py` loads the matching frozen reference/schema for each call and delegates all four calls to one injected client. `scripts/vlm_client.py` implements the verified Responses request contract: Bearer authentication, inline PNG/JPEG data URLs, `instructions`, user `input_text`, `input_image`, and `max_output_tokens: 4000`. It traverses message content to find `output_text`, performs strict `json.loads`, and does not send an unverified structured-output parameter. Production configuration remains fail-closed and never falls back to Fake or Interactive.

`RuntimeConfig.repeated_instance_semantic_limit` defaults to `2`; use `None` for all instances. Extra instances remain complete non-terminal Node Records with `status: deferred` and `deferred_reason: repeated_instance_semantic_limit`, but are not scheduled. `restore_deferred(...)` provides the minimal deterministic `deferred -> pending` transition. A run with idle active queues and preserved deferred branches reports `complete_with_deferred`, not fully decomposed.

Creation provenance is only a shortcut for unresolved state. An already consistent `node_role` / `terminal` / `next_action` state takes priority, including an `expand_instances` child that later became an asset through `stop_as_asset`. `add_semantic_warning(...)` records non-operative review metadata in runtime state and the run manifest; it never changes a Node, retries an adapter, or changes `complete` / `complete_with_deferred` into failure.

For real-image interactive execution, `scripts/interactive_file_adapter.py` implements the same synchronous Adapter interfaces through durable JSON request/response files. Start with:

```powershell
python scripts/run_recursive_runtime.py --adapter interactive --run-dir runs/my-r5 --root-node-crop path\to\node-crop.png --validation-mode real_image
```

When the command prints `WAITING_FOR_ADAPTER`, inspect the reported Analysis Image and write the frozen contract result inside `adapter-responses/<request-id>.json`. Then continue without editing Python:

```powershell
python scripts/run_recursive_runtime.py --adapter interactive --run-dir runs/my-r5 --resume
```

Waiting is a normal persisted state, not `failed`, `blocked`, or completed. The same unanswered request retains its request ID across resumes. Response envelopes are validated by `schemas/interactive-adapter-response.schema.json`; the contained `result` is still validated by its existing frozen Router/strategy validator. Runtime remains responsible for queues, transforms, crops, children, depth, and deferred policy.

Run manifests record `validation_mode`, per-strategy `adapter_types`, and `real_visual_inference_used`. Fake/fixture runs are valid only as mechanics evidence and cannot be configured as `real_image`; interactive/production visual adapters may be used for real-image execution. `real_visual_inference_used` becomes true only after a valid interactive response is consumed or a real production client response passes frozen validation and is consumed by Runtime. Adapter construction alone does not set it. The earlier hard-coded Backpack debug run is mechanics evidence only; real-image R5 remains pending.

See `references/recursive-runtime-v0.1.md` for the complete engineering contract and the Runtime/VLM responsibility boundary.

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
