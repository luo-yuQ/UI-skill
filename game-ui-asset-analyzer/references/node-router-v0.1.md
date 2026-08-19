# Stage2-A Node Router v0.1.2

Status: **contract implemented / hierarchy-boundary stability patch**.

This file is the production visual-model prompt and engineering reference for classifying exactly one Current Node. The Router chooses the next cut type by returning `node_role`; it does not produce children or execute a route.

Router v0.1.2 retains the v0.1 role enum, output schema, deterministic action mapping, and historical validation evidence. It adds a hierarchy-preservation boundary without changing Router output or Runtime behavior.

## Production prompt

You are performing Stage2-A Node Router v0.1.2 on exactly one Current Node Analysis Image.

Determine the Current Node's **primary organizational role** so that the next component-tree step produces stable, semantically meaningful Direct Children. Choose the type of the next cut; do not perform ordinary appearance classification. Prefer stable coarse semantics over unstable fine decomposition. Prioritize role stability, tree-level stability, and stable Direct Children over bbox detail or finer granularity.

Return exactly one `node_role` from:

```text
structural_group
repeated_group
component_instance
asset
```

Use these definitions:

- `asset`: the Current Node is already one coherent visual asset and further recursion has no clear engineering value. Do not split its highlight, shadow, internal symbol, baked-in text, texture, or painted details.
- `repeated_group`: the Current Node's primary identity is a collection of enumerable peer instances with the same component/business semantics and the same or highly similar schema, differing mainly in instance data.
- `component_instance`: the Current Node is one concrete, self-contained UI/business component whose immediate owned Direct Children are visual assets. A node may contain multiple internally distinct visual elements with different responsibilities and still be a single `component_instance` when those parts together form one self-contained UI object and its next natural decomposition is directly into visual assets such as artwork, icon, text, status, or controls. Being self-contained is not sufficient: this role is allowed only when direct semantic decomposition would preserve every independently meaningful ownership boundary.
- `structural_group`: the Current Node's next natural Direct Children are meaningful structural regions, sections, containers, or collections, including evidence-backed subcomponents, and another `structural_split` would materially reduce visual complexity before asset decomposition while preserving those ownership boundaries.

Apply these boundaries before deciding:

- Mixed composition takes priority over collection area: if an important independent sibling region and a repeated collection both deserve Direct Children, choose `structural_group` so the sibling is preserved. Ignore tiny decoration, frame texture, corner ornaments, and light effects when deciding whether such a structural sibling exists.
- Visual similarity alone does not make a `repeated_group`; peers must be enumerable instances of the same component or business object.
- Repetition inside one self-contained component does not automatically make the parent a `repeated_group`. If the repeated collection is the parent's primary identity, choose `repeated_group`; if it is one meaningful child alongside other independent siblings, choose `structural_group` and preserve the collection as one Direct Child.
- Do not classify a node as `structural_group` merely because its internal visual assets serve different responsibilities.
- When the boundary is uncertain, ask: if this node were decomposed one level next, what kind of Direct Children would naturally be produced? If they are mainly icon, illustration, text, button, status, or decoration, favor `component_instance`. If they are mainly header region, content region, sidebar, section, collection, panel group, or functional area, favor `structural_group`.
- A local selection frame, tooltip, floating hint, temporary overlay, state marker, or small occlusion normally does not change the parent's primary role.
- Judge the Current Node and its candidate next layer. You may inspect whether a candidate Direct Child visibly owns its own internal assets solely to avoid skipping that child, but do not infer or output recursive descendants.

### Flattening Guard

Before choosing `component_instance`, verify that semantic decomposition would expose only the Current Node's immediate owned Direct Children. It must not skip an independently meaningful intermediate ownership boundary such as a container, collection, repeated collection, slot, card, row, cell, item instance, or subcomponent.

- Being self-contained is not sufficient for `component_instance`; complete modules can still own meaningful structural children.
- Apparent visual assets are not direct children of the Current Node when a nearer component owns them. Preserve that nearer component first.
- If multiple peer instances each own their own visual assets, do not flatten the instances into those assets. When the collection is one part of a larger mixed module, use `structural_group` and preserve the collection as one Direct Child. When the Current Node is essentially the collection itself, use `repeated_group`.
- Do not use the number of visible assets, taxonomy categories, visual complexity, or bbox relationships as a mechanical threshold.

### Anti-over-splitting Guard

Do not invent an intermediate node merely to satisfy the ownership concept. Preserve a candidate boundary only when the image supports a stable, independently meaningful component-tree unit whose identity or ownership makes later analysis more focused.

- Do not create a wrapper that only renames or regroups the same direct visual assets without adding meaningful component ownership.
- Visual complexity, asset count, or different asset responsibilities alone are not evidence for an intermediate node.
- A single component whose immediate children are a base, artwork, icon, text, status, control, or decoration remains `component_instance`, even when those assets are visually diverse.
- Do not classify every enclosing shape, alignment cluster, or decorative frame as a container or subcomponent.

Use this decision check, subject to both guards and the mixed-composition boundary above:

1. If the node is already a coherent visual asset with no meaningful next split, choose `asset`.
2. If its natural Direct Children are same-type, same-business peer instances and the parent is essentially their collection, choose `repeated_group`.
3. If its next natural Direct Children include one or more evidence-backed structural regions, containers, collections, or subcomponents that must be preserved before their owned assets, choose `structural_group`.
4. Only if it is one concrete component whose immediate owned Direct Children are directly visual assets, choose `component_instance`, even when those assets have different responsibilities.

The input is the existing deterministic Current Node Analysis Image. It is produced by resizing the Node Crop to width 1024 while preserving aspect ratio under the frozen Coordinate Contract. Do not request another resize, output a bbox, or perform a coordinate transform.

Return JSON only, conforming to `schemas/node-route.schema.json`, in exactly this shape:

```json
{
  "node_role": "structural_group",
  "confidence": 0.95,
  "reason": "One short reason."
}
```

Return no Markdown, children, bboxes, `next_action`, taxonomy, tree, repeated instances, structural regions, or extraction strategy. `confidence` is diagnostic only: it is not a correctness signal, cannot determine PASS, and cannot replace contract validation. Do not let confidence change the selected role.

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

`resolve_node_action(role)` raises an error for every role outside the frozen enum. The VLM never emits or selects `next_action`. Router v0.1.2 does not execute any mapped action.

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

Node Router v0.1.2 classifies only. This contract does not modify or execute `structural_split`, `expand_instances`, `semantic_decompose` v0.1.2, Recursive Runtime, its unchanged ten-category taxonomy enum, Stage2-B or later stages, extraction, FairyGUI, or XML.
