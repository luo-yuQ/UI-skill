# Stage2-A `expand_instances` v0.1

Status: **FROZEN**. Future behavior changes require a new version.

This is the production visual-model prompt and short engineering reference for the already-routed action:

```text
repeated_group
-> expand_instances
```

It identifies one level of peer component instances. It does not classify the parent, decompose instance assets, traverse a tree, or review its own output.

## Production prompt

You are performing Stage2-A `expand_instances` v0.1 on exactly one current `repeated_group` Analysis Image. The caller has already classified the parent; do not run the Router again.

Identify only the collection's **Direct Child Instances** that share the same primary component structure or schema.

- Return peer instances of one component template, even when their artwork, icons, text, numbers, titles, runtime data, selection state, or enabled state differ.
- Do not split an instance into icons, text, illustrations, frames, buttons, or other internal assets.
- Do not emit the collection background, title, outer container, tooltip, floating message, selection frame, or local overlay as an instance.
- A local state marker, tooltip, overlay, or partial occlusion does not by itself change template identity or justify omitting an otherwise reliable instance.
- Make each bbox cover the complete component instance, not only its most visually salient internal content.
- Set `partial_instance` to `true` when the instance belongs to the collection but is not fully visible because of the Node Crop boundary, occlusion, or another visibility limit. This flag does not mean low confidence, invalidity, or deletion.
- Emit a partial instance only when its full component bbox can still be estimated reliably.
- Stay at one level and prefer stable complete instances.

Use one concise, dynamic, human-readable `instance_type` for the shared component template. It is a label, not a frozen taxonomy. Set `repeat_count` to exactly the number of returned `instances`. Give every instance a unique non-empty `id`, integer-pixel `bbox`, `partial_instance`, and confidence from 0 through 1. Give one brief non-empty reason.

Every bbox uses the provided **Analysis Image** coordinate space with a top-left origin. The caller has deterministically resized the Node Crop to the frozen analysis width of 1024 pixels while preserving aspect ratio. Do not use normalized coordinates, a model-reported canvas, Node Crop pixels, or an inferred model resize.

Return JSON only, conforming to `schemas/expand-instances.schema.json`, in this shape:

```json
{
  "instance_type": "dynamic human-readable label",
  "repeat_count": 1,
  "instances": [
    {
      "id": "instance_001",
      "bbox": {
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1
      },
      "partial_instance": false,
      "confidence": 0.0
    }
  ],
  "reason": "brief reason"
}
```

Return no taxonomy, `node_role`, `next_action`, internal assets, semantic decomposition, recursive descendants, or review result. Every returned instance is a component instance that may re-enter the Router only in a future orchestration layer.

## Direct-child boundary

Perform only:

```text
RepeatedCollection
|-- ComponentInstance_001
|-- ComponentInstance_002
`-- ComponentInstance_N
```

Do not replace those instances with their internal visual assets; that belongs to `semantic_decompose` after a later routing step.

## Coordinate Contract v0.1

The frozen caller-owned flow is:

```text
Node Crop
-> shared deterministic Analysis Image preparation
-> Analysis Image
-> expand_instances bbox in Analysis Image pixels
-> deterministic transform
-> bbox in Node Crop pixels
```

Use the shared `scripts/prepare_analysis_input.py --max-width 1024 --force-width`; do not add task-specific resize logic. The VLM and validator never perform the transform.

## Engineering contract

Validate the immutable VLM JSON with `schemas/expand-instances.schema.json` and `scripts/validate_expand_instances.py --analysis-image <actual-image>`. The validator checks document shape, `repeat_count`, required fields, unique IDs, numeric ranges, and bounds against the real Analysis Image. It does not judge visual template identity, infer missing instances, or change the document.

Render optional human-review evidence with `scripts/render_instances_overlay.py`. The renderer reuses the deterministic overlay layout utilities and never participates in a semantic decision or self-review loop.

## Validation Evidence

Three real repeated-group cases established the frozen behavior; these observations are evidence, not production-prompt examples:

- **E01 — large two-dimensional collection:** identified all 25 peer instances.
- **E02 — horizontal status collection:** identified two complete peer instances.
- **E03 — vertical preview collection:** identified five peer rows and marked the boundary-clipped final row with `partial_instance: true`.

Historical experiment JSON remains immutable and should be checked with the formal validator whenever those artifacts are available.
