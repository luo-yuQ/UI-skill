# Stage2-B1 Extraction Layer v0.1

Status: **CONTRACT FROZEN**. Backend implementations and deterministic planning policies may improve without changing this interface.

## Responsibility boundary

```text
Stage2-A: semantic understanding and immutable asset-leaf ownership
Stage2-B1: deterministic extraction planning, execution, and mechanical quality gates
Stage2-C: repair and missing-pixel recovery
```

B1 consumes exactly one Stage2-A asset leaf with `asset_id`, `node_id`, `taxonomy`, `bbox`, and `source_crop`. It preserves those values as immutable lineage. It does not call a VLM, change a bbox, reclassify an asset, discover assets, repair missing pixels, or alter Stage2-A Runtime or Router behavior.

## Layer 1: Extraction Planner

`ExtractionPlanner` emits an `ExtractionPlan` conforming to `schemas/extraction-plan.schema.json`. The frozen modes are `direct_crop`, `foreground_extract`, and `repair_required`. The frozen backends are `direct`, `color_distance`, `grabcut`, and `unknown`.

Planning is extensible through `PlanningPolicy`. A policy must use deterministic evidence and must not infer a mode from taxonomy alone. The default `ConservativePlanningPolicy` is deliberately taxonomy-independent and returns `repair_required` when no sufficient deterministic evidence exists.

Valid mode/backend pairs are:

| extraction_mode | backend |
| --- | --- |
| `direct_crop` | `direct` |
| `foreground_extract` | `color_distance`, `grabcut`, or `unknown` |
| `repair_required` | `unknown` |

## Layer 2: Extraction Executor

`ExtractionExecutor` consumes an already-valid plan. It verifies that `asset_id`, `node_id`, `taxonomy`, `bbox`, and `source_crop` still match the Stage2-A leaf before executing anything.

- `direct_crop` deterministically crops the frozen bbox and returns PNG bytes.
- `foreground_extract` dispatches to an injected `ForegroundBackend`; B1 v0.1 defines the interface but does not freeze an algorithm or tune parameters.
- `repair_required` is deferred to Stage2-C and is never executed as a B1 extraction.

## Layer 3: Quality Gate

`ExtractionQualityGate` operates only on plan lineage and backend metrics. It can reject empty output, empty masks, out-of-bounds bboxes, abnormal foreground/background ratios, and extraction failures. It contains no asset names, colors, taxonomy shortcuts, or other visual keyword rules.

The gate thresholds are carried in each plan so results remain auditable. Ratio checks apply only when a foreground backend reports mask metrics.
