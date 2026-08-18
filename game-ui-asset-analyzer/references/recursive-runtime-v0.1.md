# Recursive Runtime v0.1

Status: **IMPLEMENTED / R5 real-image validated / awaiting R6 generalization**

Freeze status: not frozen. R5 Backpack integration validated the Runtime mechanics; R6 generalization remains pending.

## Responsibility boundary

The Runtime owns Node state, per-level queues, adapter call order, deterministic provenance shortcuts, bbox transforms, recursive child crops and Analysis Images, parent-child relations, depth, tree persistence, terminal scheduling, and the repeated-instance cost policy. It does not infer visual semantics from filenames, labels, grids, item counts, or domain terms.

The VLM-facing adapters own only their frozen scoped outputs:

- `RouterAdapter.route(analysis_image)` returns the frozen Node Router document.
- `StructuralSplitAdapter.run(analysis_image)` returns a frozen `structural_split` document.
- `ExpandInstancesAdapter.run(analysis_image)` returns a frozen `expand_instances` document.
- `SemanticDecomposeAdapter.run(analysis_image)` returns a frozen `semantic_decompose` document.

Every result is passed to its existing frozen validator. The Runtime contains no prompt text, provider code, workspace search, retry framework, verifier, or visual fallback guessing. `scripts/fake_runtime_adapters.py` provides deterministic fixtures only.

Adapter is required; a specific provider is optional. Runtime Core needs a contract-compatible Strategy Adapter for the action it is about to execute, but that adapter may be backed by a production VLM provider, TRAE test integration, fake, fixture, or a future provider. Autonomous production execution may require a concrete production implementation, but the absence of one is not a Runtime-wide blocked condition. If the current action has no injected adapter, the current Node records `adapter_unavailable` as a failure under the existing error model.

## Level-by-level execution

`RecursiveRuntime.run()` is serial and breadth-first. A node produced while processing depth N is recorded immediately in the tree, but a non-terminal child can only enter `next_level_queue`. Once `current_level_queue` is empty, and only then, the runtime performs:

```text
current_level_queue = next_level_queue
next_level_queue = []
current_depth += 1
```

Terminal asset children are persisted but never queued. Deferred children are persisted but not placed in either active queue. Empty active queues therefore mean active execution is complete; they do not mean every recorded branch is fully decomposed.

## Node Record and lifecycle

`NodeRecord` in `scripts/recursive_runtime.py` is the unified runtime record. Its required core is `node_id`, `parent_id`, `depth`, `produced_by`, `node_role`, `terminal`, `next_action`, `requires_router`, optional artifact paths and parent-coordinate bboxes, and `status`. Source-specific metadata such as `source_instance_id`, `instance_type`, `partial_instance`, `taxonomy`, `label`, and `confidence` is optional.

Supported statuses are:

- `pending`: present in an active queue and waiting to execute.
- `running`: currently processing.
- `ready`: action resolved but its strategy has not completed.
- `done`: this node's required action completed.
- `deferred`: valid and resolved, but intentionally not scheduled by cost policy.
- `failed`: adapter output, validation, artifact creation, or another execution step failed.
- `blocked`: safety or contract policy prohibits execution.

`terminal` and `status` are independent. For example, a non-terminal component instance becomes `done` after successful semantic decomposition.

## Current state and provenance shortcuts

Current semantic state has higher priority than creation provenance. If `node_role`, `terminal`, and `next_action` already form a complete consistent state, the Runtime validates and preserves them without applying a provenance shortcut. Thus an `expand_instances` child that later became `asset -> stop` through `stop_as_asset` remains an asset during later processing or resume. Provenance describes how the Node was created; the current fields describe what it is now.

If current state is unresolved, the Runtime delegates deterministic inference to the frozen Asset / Stop resolver:

- Valid `semantic_decompose` taxonomy -> `asset`, terminal, `stop`, no Router.
- `expand_instances` provenance -> `component_instance`, non-terminal, `semantic_decompose`, no Router.
- Unclassified `structural_split` provenance -> non-terminal and Router required.
- An unknown node without a reliable shortcut -> Router required.

An explicit but inconsistent state, such as `node_role: asset`, `terminal: false`, and `next_action: structural_split`, is a contract failure. Provenance never silently repairs it.

The only executable actions are `structural_split`, `expand_instances`, `semantic_decompose`, and `stop`.

## Child creation and coordinates

All adapter bboxes are interpreted in the current Node Analysis Image. `scripts/runtime_geometry.py` maps all four bbox edges into the original parent Node Crop by reusing `build_asset_analysis.map_bbox_to_source`; this applies independent actual x/y scale factors, rounding, clamp, and non-empty validation.

Recursive children from `structural_split` and `expand_instances` are cropped from the parent's original `node-crop.png`, never its resized Analysis Image. Their Analysis Images are generated by the shared `prepare_analysis_input.prepare_analysis_input(..., max_width=1024, force_width=True)` utility, preserving proportional rounded height.

`structural_split` children have no inferred role, set `requires_router: true`, and enter the next-level queue. `expand_instances` children preserve source instance metadata and deterministically shortcut to `component_instance -> semantic_decompose`.

For `semantic_decompose` decision `decompose`, each valid visual child becomes an `asset` Node Record with both parent-coordinate bboxes, `terminal: true`, `next_action: stop`, and `status: done`. It receives no Node Crop or Analysis Image and is never queued. For `stop_as_asset`, the current component instance itself becomes the asset; no duplicate child is created.

## Store, state, and run result

`NodeStore` rejects duplicate IDs, reads and updates nodes by ID, preserves ordered parent-child relations, and writes the complete tree snapshot. A run persists:

```text
runs/<run-id>/
|-- run-manifest.json
|-- runtime-state.json
|-- tree.json
`-- nodes/<node-id>/
    |-- node.json
    |-- node-crop.png             # root/recursive input nodes, not asset children
    |-- analysis-image.png        # root/recursive input nodes, not asset children
    |-- router-result.json        # when Router was required
    `-- strategy-result.json      # when a strategy ran
```

The run result is `complete`, `complete_with_deferred`, `failed`, or `blocked`. `run-manifest.json` separately records `active_execution_complete`, `fully_decomposed`, `runtime_failures`, and `semantic_warnings`.

## Runtime failures and semantic warnings

A `runtime_failure` is a mechanics or contract failure, such as a duplicate Node ID, orphan child, queue corruption, invalid action/state mapping, recursive continuation of an asset, unavailable required adapter, or coordinate-transform contract failure. These failures participate in `run_result`.

A `semantic_warning` is non-operative quality metadata, such as a questionable Router classification, possible repeated-instance under-detection, or a visually disputed semantic decomposition. `RecursiveRuntime.add_semantic_warning(...)` stores `{node_id, source, type, message}` in `runtime-state.json` and copies the list into `run-manifest.json`.

Semantic warnings never mutate Node fields or bboxes, trigger retry, call a VLM again, or alter the tree. When mechanics, queues, and tree integrity pass, warnings do not change `complete` or `complete_with_deferred` into `failed` or `blocked`. Runtime v0.1 does not automatically resolve semantic warnings.

## Repeated-instance cost policy

`RuntimeConfig.repeated_instance_semantic_limit` defaults to `2`; `None` means all. Selection follows adapter output order, so the first N instance records are always scheduled. Remaining instances are still fully recorded with their bbox, parent, source ID/type, `next_action: semantic_decompose`, `terminal: false`, `status: deferred`, and `deferred_reason: repeated_instance_semantic_limit`.

`RecursiveRuntime.restore_deferred(node_id)` performs the minimal deterministic `deferred -> pending` restoration and can schedule the node when the active queues are idle. It is not a general resume engine.
