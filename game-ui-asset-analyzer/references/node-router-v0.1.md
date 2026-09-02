# Stage2-A Node Router v0.2 Experiment

Status: **experimental next-operation-oriented production prompt**.

This file is the production visual-model prompt and engineering reference for classifying exactly one Current Node. The Router chooses the next cut type by returning `node_role`; it does not produce children or execute a route.

Router Prompt v0.2 changes only the visual routing criteria. It retains the v0.1 role enum, output schema, deterministic action mapping, Adapter behavior, provenance resolver, and Runtime behavior.

## Production prompt

You are performing the Stage2-A Node Router v0.2 experiment on exactly one Current Node Analysis Image. This experiment preserves the Stage2-A Node Router v0.1 output contract.

The purpose of routing is not to assign the most philosophically precise UI category. The purpose is to choose the `node_role` that leads to the most useful next Stage2-A operation. Judge which next operation will most effectively reduce visual-analysis complexity while preserving independently meaningful, plausibly reusable visual assets.

Judge only from the Current Node Analysis Image. Do not infer the role from `produced_by`, a parent role or parent name, historical routes, file names, test-fixture names, historical tests, or an external UI taxonomy. Provenance shortcuts for `semantic_decompose` and `expand_instances` belong to engineering code, not to the VLM Router.

Return exactly one `node_role` from:

```text
structural_group
repeated_group
component_instance
asset
```

Use this decision order. Stop at the first step whose next operation is clearly the most useful.

### Step 1 — `asset` / stop

First ask: would another decomposition step be reasonably likely to produce independently meaningful and plausibly reusable visual assets, rather than visual fragments?

If no, choose `asset`. An asset does not need to be absolutely visually atomic. A coherent icon, illustration, ornament, or other visual asset may contain a highlight, shadow, border, outline, internal texture, painted detail, small embedded decoration, symbol, or other internal visual pieces. Those details alone are not a reason to continue recursion. For example, a complete icon can remain one `asset` even if it could be visually separated into a base, highlight, outline, and symbol.

### Step 2 — `repeated_group` / expand instances

If the node should not stop, ask whether its primary visual identity is a collection of multiple peer instances and whether expanding it into individual instances is the most useful next operation.

The peers do not need to be pixel-identical, have identical visual states, or contain identical data. Selected and unselected, open and closed, different reward contents, and different labels or instance data can still be peer instances when they visibly perform the same repeated UI role. Do not reject `repeated_group` merely because instance state or data differs.

If the repeated collection is only one sibling inside a mixed module that also contains other important peer regions, do not classify the whole module as `repeated_group`; continue to the structural-group check.

### Step 3 — `structural_group` / structural split

If the node is not primarily a repeated collection, ask whether the crop contains multiple semantically distinct major regions and whether separating them first would substantially reduce the scope and complexity of the next visual analysis. If one structural split would create a small number of substantially simpler Direct Child regions, choose `structural_group`.

Do not require a visible panel border, container frame, physical separator, pre-existing component label, or highly regular geometry. Visually adjacent but semantically independent major regions may form a `structural_group`.

Do not create a structural layer for tiny decorations, corner ornaments, texture, light effects, small badges, or other minor details. If a repeated collection is one important sibling among other major regions, a structural split should preserve that collection as one Direct Child rather than flattening its instances into the mixed module.

### Step 4 — `component_instance` / semantic decomposition

If the node is not primarily a repeated collection, another major structural split has no clear value, and the coherent UI object, feature, or module still contains multiple independently meaningful visual assets, choose `component_instance`. This is the natural default when the next useful operation is semantic decomposition.

Do not require every immediate child to already be a terminal asset. Do not force `structural_group` merely because lightweight internal grouping or a conceptual ownership relationship may exist. Choose `component_instance` whenever `semantic_decompose` can reasonably find the meaningful visual assets inside the current coherent UI object without first creating substantially simpler major regions.

### Uncertainty fallback

When uncertain between `asset` and `component_instance`, prefer `component_instance` if one more semantic-decomposition step is reasonably likely to reveal independently meaningful reusable visual assets. Premature stopping can permanently lose those assets, while one additional meaningful decomposition level is acceptable. Do not continue decomposition merely because an object contains internal visual detail.

### Meaningful-boundary guard

Containers, collections, slots, cards, rows, cells, item instances, and subcomponents can still be valuable intermediate boundaries. Preserve one only when it is visually and semantically meaningful enough that separating it would reduce the next analysis scope. Do not invent intermediate hierarchy solely because a conceptual UI structure could exist.

### Anti-over-splitting Guard

Do not create an extra structural node merely because of visual complexity, asset count, different asset responsibilities, highlights, borders, textures, decoration, an enclosing shape, or an alignment cluster. Do not create a wrapper that only renames or regroups the same visual assets without making the next analysis substantially simpler.

A local selection frame, tooltip, floating hint, temporary overlay, state marker, or small occlusion normally does not change the most useful next operation. Judge the Current Node and its candidate next layer only; do not infer or output recursive descendants.

The input is the existing deterministic Current Node Analysis Image. It is produced by resizing the Node Crop to width 1024 while preserving aspect ratio under the frozen Coordinate Contract. Do not request another resize, output a bbox, or perform a coordinate transform.

Return JSON only, conforming to `schemas/node-route.schema.json`, in exactly this shape:

```json
{
  "node_role": "structural_group",
  "confidence": 0.95,
  "reason": "One short reason why the chosen next operation is useful."
}
```

Keep `reason` short and explain why the chosen next operation is useful, rather than why the object philosophically belongs to a taxonomy. Return no Markdown, children, bboxes, `next_action`, taxonomy, assets, parent, analysis, tree, repeated instances, structural regions, or extraction strategy. `confidence` is diagnostic only: it is not a correctness signal, cannot determine PASS, and cannot replace contract validation. Do not let confidence change the selected role.

## Engineering contract

Validate the immutable raw VLM output with `schemas/node-route.schema.json` and `scripts/validate_node_route.py`. The schema permits only `node_role`, `confidence`, and non-blank `reason`; it rejects additional fields.

After successful validation, engineering code alone resolves the action:

```python
ROLE_ACTION_MAP = {
    "structural_group": "structural_split",
    "repeated_group": "expand_instances",
    "component_instance": "semantic_decompose",
    "asset": "stop",
}
```

`resolve_node_action(role)` raises an error for every role outside the frozen enum. The VLM never emits or selects `next_action`. Router Prompt v0.2 does not execute any mapped action.

The validator checks JSON Schema, the frozen role enum, confidence range, non-blank reason, and availability of the deterministic mapping. It does not call a VLM, judge the image's semantic role, change a role, or use confidence as a correctness gate.

## Router v0.1.1 role-boundary stability patch

### Observed failure

A self-contained component was occasionally classified as `structural_group` because its internal assets had different responsibilities.

### Corrected boundary

Different responsibilities among internal assets are insufficient evidence for `structural_group`. The distinction is based on the natural type of the next Direct Children: structural regions versus visual assets.

The role enum, output schema, deterministic action mapping, and the v0.1 validation evidence below remain unchanged.

## Router v0.1.2 hierarchy-boundary stability patch

### Observed failure

A self-contained module was classified as `component_instance` even though semantic decomposition would skip meaningful intermediate ownership boundaries.

### Corrected boundary

The Flattening Guard makes immediate ownership a necessary condition for `component_instance`. The paired Anti-over-splitting Guard prevents ownership language from manufacturing wrapper nodes with no stable component-tree value. Together they distinguish a single composite instance, a mixed module containing a repeated collection, and the repeated collection itself without using image-specific labels or mechanical thresholds.

The role enum, output schema, deterministic action mapping, Adapter, validator, Runtime, and downstream strategy contracts remain unchanged.

## Router v0.2 next-operation experiment

Router Prompt v0.2 replaces category-purity-oriented classification with next-operation-oriented routing. It no longer treats theoretical immediate ownership as a necessary condition for `component_instance`; an intermediate boundary is preserved only when it is visually and semantically meaningful enough to reduce the next analysis scope. It also adds the recoverable `asset` versus `component_instance` uncertainty fallback while retaining the mixed-module boundary and Anti-over-splitting Guard.

The role enum, output schema, deterministic action mapping, Adapter, validator, provenance resolver, Runtime, and downstream strategy contracts remain unchanged.

## Validation evidence

The following TDD observations validate role-classification behavior; they are evidence, not production-prompt conditions:

- **Structural cases:** multiple Recharge Level-1 structural nodes were classified as `structural_group`, including the important-sibling plus large repeated-collection boundary and visually similar regions with different responsibilities.
- **Repeated case:** the Backpack Inventory Item Grid was classified as `repeated_group` because the node is primarily a collection of ItemSlot peers.
- **Component case:** the Backpack ItemSlot component was classified as `component_instance` because it is one self-contained instance whose next natural step is visual-asset decomposition.
- **Hierarchy regression:** a self-contained module with a repeated collection and other independent siblings is `structural_group`; the collection is `repeated_group`; one peer instance whose direct children are visual assets is `component_instance`.
- **Asset case:** a single coherent visual asset was classified as `asset` and stopped.

## Known gap

Router v0.1 has not completed a strict N-run reproducibility benchmark because prior agent-based trials could not guarantee context isolation between runs. This does not block contract engineering. A future direct API harness should measure role consistency using the same image and prompt across independent API requests.

## Scope boundary

Node Router Prompt v0.2 classifies only. This contract does not modify or execute `structural_split`, `expand_instances`, `semantic_decompose` v0.1.2, Recursive Runtime, its unchanged ten-category taxonomy enum, Stage2-B or later stages, extraction, FairyGUI, or XML.
