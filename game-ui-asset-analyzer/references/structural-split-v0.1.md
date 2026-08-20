# Stage2-A `structural_split` v0.1

Status: **FROZEN**. Future behavior changes require a new version.

This is the production visual-model prompt and short engineering reference for the already-routed action:

```text
structural_group
-> structural_split
```

It identifies one level of stable structural regions. It does not classify the parent, expand repeated instances, decompose visual assets, traverse a tree, or review its own output.

## Production prompt

You are performing Stage2-A `structural_split` v0.1 on exactly one current Analysis Image. The caller supplies either normal routed mode, where the Router selected `structural_group`, or fallback probe mode, where the role is deliberately unconfirmed. In probe mode, do not assume the node is a `structural_group` and do not invent children merely to satisfy the task.

Identify only the most natural, stable **Direct Children** whose different responsibilities make the next visual-analysis step materially more focused.

- Split one level only; do not emit descendants.
- Prefer stable, coarse structural regions with distinct responsibilities.
- Keep a repeated collection whole at this level; do not expand its instances.
- Do not emit icons, text, buttons, illustrations, or other visual assets directly. A title or status may remain inside a structural child when that region has an independent structural responsibility.
- Ignore decoration, frame corners, glow, texture, and other small details that do not materially help the next analysis step.
- Do not create a child that is nearly the whole parent when the visual complexity is not reduced.
- Visual similarity alone does not make regions repeated instances when they serve different responsibilities.

If no useful structural split exists, set `no_useful_structural_split` to `true`, return `children: []`, and give a brief reason. Otherwise set it to `false` and return at least one child. Give every child a unique non-empty `id`, a concise dynamic `label`, an integer-pixel `bbox`, and confidence from 0 through 1.

Every bbox uses the provided **Analysis Image** coordinate space with a top-left origin. The caller has deterministically resized the Node Crop to the frozen analysis width of 1024 pixels while preserving aspect ratio. Do not use normalized coordinates, a model-reported canvas, Node Crop pixels, or an inferred model resize.

Return JSON only, conforming to `schemas/structural-split.schema.json`, in this shape:

```json
{
  "no_useful_structural_split": false,
  "children": [
    {
      "id": "child_001",
      "label": "dynamic label",
      "bbox": {
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1
      },
      "confidence": 0.0
    }
  ],
  "reason": "brief reason"
}
```

Return no child role, `next_action`, taxonomy, repeated instances, visual assets, recursive descendants, or review result. Every child re-enters the Router in a future orchestration layer.

## Coordinate Contract v0.1

The frozen caller-owned flow is:

```text
Node Crop
-> deterministic proportional resize
-> Analysis Image
-> structural_split bbox in Analysis Image pixels
-> deterministic transform
-> bbox in Node Crop pixels
```

Use the shared `scripts/prepare_analysis_input.py --max-width 1024 --force-width`; do not add task-specific resize logic. The VLM and validator never perform the transform. The default non-node workflow remains width-capped and does not upscale unless `--force-width` is supplied.

## Engineering contract

Validate the immutable VLM JSON with `schemas/structural-split.schema.json` and `scripts/validate_structural_split.py --analysis-image <actual-image>`. The validator checks document shape, decision consistency, required fields, unique child IDs, numeric ranges, and bounds against the real Analysis Image. It does not judge labels or visual semantics and never changes a bbox.

Render optional human-review evidence with `scripts/render_structural_overlay.py`. The overlay is read-only debugging output and never participates in a semantic decision or self-review loop.

## Validation Evidence

Four real UI cases established the frozen behavior; these observations are evidence, not production-prompt examples:

- **S01 Header — decoration suppression:** kept navigation/identity and the resource-status collection without manufacturing a child for center decoration.
- **S02 Crystal Store — repeated collection preservation:** kept the title region and the complete repeated product collection without expanding its twelve instances.
- **S03 Account Perks — coarse structural decomposition:** kept three responsibility-level regions without crossing into icons, text, a progress bar, or a button.
- **S04 Payment Footer — visual similarity is not repeated-instance identity:** kept three visually similar regions as structural children because they serve different responsibilities.

Historical experiment JSON remains immutable and should be checked with the formal validator whenever those artifacts are available.
