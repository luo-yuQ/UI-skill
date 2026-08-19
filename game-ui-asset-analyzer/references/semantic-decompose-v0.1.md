# Stage2-A `semantic_decompose` v0.1

Status: **FROZEN contract candidate**. Freeze is complete only after the three recorded validation runs pass the formal validator. Future behavior changes require a new version.

This file is the production visual-model prompt and short behavior reference for the already-selected Stage2-A strategy:

```text
component / component_instance
-> semantic_decompose
```

It does not select a strategy, classify `node_role`, traverse a tree, schedule another branch, or extract pixels.

## Production prompt

You are performing Stage2-A `semantic_decompose` v0.1 on exactly one current node. The caller has already established that `node_role` is `component` or `component_instance`.

Your job is to identify only the current component's **Direct Children** that deserve to exist as independent visual assets. A child must have at least one meaningful engineering reason to exist independently: it can be a standalone visual asset, has independent UI semantics, may be replaced or shown/hidden independently, may bind independent runtime data, or gives Stage2-B a reasonable reason to extract it separately. Do not create children merely to use a taxonomy category.

### Direct-child and ownership rules

- Decompose one level only. Do not describe grandchildren or how an artwork was painted.
- Protect coherent artwork. Keep an illustration or icon whole when its border, glow, shadow, internal symbol, painted text, label plate, local decoration, highlight, or texture jointly forms one visual asset.
- Decompose independent engineering assets, not every visible detail.
- Context padding may show neighboring UI, but context is visible, not owned. Never claim a neighboring slot, text, icon, frame, or background as a child of the current component.
- A parent-level shared background is not owned by the current component.

### Text semantics

- Keep baked-in text inside its coherent logo, emblem, badge, illustration, icon, decorative plate, or other artwork. OCR readability does not make it an independent `text` asset.
- Emit `taxonomy: text` when lettering or numbers form an independent layer, plausibly change at runtime, or carry independent runtime data such as a quantity, count, or status value.
- Decide from layer semantics, not from language, alphabet, or whether the content is numeric.
- When text is a child, its bbox must include every complete character. A multi-character or multi-digit value must not be reduced to one character.

### Frozen taxonomy

Every emitted child, and the leaf taxonomy used by `stop_as_asset`, must use exactly one of:

```text
background
panel
button
icon
illustration
frame
progress_bar
decoration
text
unknown
```

Do not add or rename categories. Prefer `icon` for a functional or symbolic visual and `illustration` for the component's principal complete pictorial content. Size alone does not decide between them; use the visual's primary role when the boundary is ambiguous.

### Frame rule

The rectangular Node Crop is not evidence of a `frame`. Emit a frame only when visible evidence shows a frame owned by this node that can reasonably exist independently or has independent UI/state meaning, such as a selected-state frame. A semantic child bbox may approach or equal the parent bbox; do not apply structural `ineffective_split` rules here.

### Decision

Return exactly one decision:

- `decompose`: at least one meaningful direct visual-asset child exists. Return one or more children and omit `asset_taxonomy`.
- `stop_as_asset`: further splitting would not produce a meaningful independent asset. Return `children: []` and classify the current node itself with `asset_taxonomy`.

Recursion is not an obligation. Never manufacture a child merely to perform the strategy.

### Bbox completeness

Every child `bbox` is an integer-pixel rectangle in the provided **Analysis Image** coordinate space, with top-left origin. Its purpose is to enclose the visual asset's complete visible extent, not merely point near it. Include the asset's complete outer contour, edges, full character strokes, shadow, glow, outer highlight, and antialiased edge. Prefer a small safety margin over visible clipping. Normal VLM or rounding variation of about one pixel is acceptable.

`bbox overlap is allowed`. Do not force bboxes to be mutually exclusive, shrink their true visual extents, or change a bbox to avoid overlap. Rectangle intersection does not prove shared pixels. Foreground occlusion, masks, extraction, and missing-pixel repair belong to later stages and must not be solved here.

Set `partial: true` only when an owned asset's visible evidence is actually clipped or incomplete at the current node boundary. The bbox must still contain its complete currently visible extent. Do not use `partial` to claim padding context.

### Coordinate Contract v0.1

The caller provides a deterministic Analysis Image derived from the Node Crop. Its width is 1024 pixels and its height is deterministically calculated from the Node Crop aspect ratio. Interpret every VLM bbox only in Analysis Image pixels:

```text
Node Crop
-> deterministic engineering resize
-> Analysis Image
-> VLM bbox in Analysis Image pixels
-> existing deterministic transform
-> bbox in Node Crop pixels
```

Do not emit normalized coordinates, reinterpret a bbox as Node Crop pixels, rely on a model-reported canvas, infer an internal model resize, or perform the Analysis Image-to-Node Crop transform. Coordinate preparation and transformation are caller responsibilities under the existing Coordinate Contract.

### Output

Return JSON only. It must conform to `schemas/semantic-decomposition.schema.json`.

The engineering caller owns and deterministically canonicalizes `task`, `node_id`, `node_role`, `bbox_constraint`, and `analysis_image_size` before validation. Do not infer their authoritative values from the image contents or filename. The examples below use structurally valid illustrative values for those fields.

For every direct child:

- Every child `id` must be a unique, non-empty string.
- Every child `label` must be a non-empty string.
- `taxonomy` must be exactly one value from the frozen taxonomy above.
- `bbox` MUST be a JSON object with exactly four fields: `x`, `y`, `width`, and `height`.
- `x`, `y`, `width`, and `height` MUST be JSON integer fields. `x` and `y` must be at least 0; `width` and `height` must be at least 1.
- Never return `bbox` as an array. `[0, 0, 100, 100]` is invalid whether interpreted as `[x1, y1, x2, y2]` or `[x, y, width, height]`.
- `partial` must be a JSON boolean: `true` or `false`. Never return the strings `"true"` or `"false"`.
- `confidence` must be a JSON number from 0 through 1.

The required bbox shape is:

```json
"bbox": {
  "x": 0,
  "y": 0,
  "width": 1,
  "height": 1
}
```

Give a concise non-empty `reason` for the decision. Use exactly one of the following complete JSON shapes and do not add fields.

#### `decompose` JSON shape

```json
{
  "node_id": "caller_owned_node_id",
  "node_role": "component",
  "task": "semantic_decompose",
  "bbox_constraint": "completeness",
  "analysis_image_size": {
    "width": 1024,
    "height": 512
  },
  "decision": "decompose",
  "children": [
    {
      "id": "child_001",
      "label": "direct visual asset",
      "taxonomy": "icon",
      "bbox": {
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1
      },
      "partial": false,
      "confidence": 0.9
    }
  ],
  "reason": "At least one meaningful direct visual-asset child exists."
}
```

#### `stop_as_asset` JSON shape

```json
{
  "node_id": "caller_owned_node_id",
  "node_role": "component_instance",
  "task": "semantic_decompose",
  "bbox_constraint": "completeness",
  "analysis_image_size": {
    "width": 1024,
    "height": 512
  },
  "decision": "stop_as_asset",
  "asset_taxonomy": "illustration",
  "children": [],
  "reason": "Further splitting would not produce a meaningful independent asset."
}
```

Do not return masks, crops, extraction strategies, repaired geometry, recursive descendants, branch schedules, or any taxonomy outside the frozen ten.

## v0.1 behavior summary

- Scope is one already-selected `component` or `component_instance` and one direct-child level.
- The unit of decomposition is an independent engineering asset, with coherent artwork protected from visual-detail over-splitting.
- Baked-in and runtime text are distinguished by layer semantics.
- `stop_as_asset` is a successful terminal decision, not a failure to recurse.
- Complete, overlapping Analysis Image bboxes are valid. No child-count, mutual-exclusion, or child-smaller-than-parent rule exists.
- The deterministic validator checks document structure and geometry only. It does not judge visual semantics.

## Validation Evidence

The v0.1 experimental evidence covers three sample types; these are validation observations, not product-level guarantees:

- **Case A - coherent artwork plus independent runtime quantity:** coherent artwork remained intact while runtime text was emitted independently.
- **Case B - logo or emblem with baked-in English text plus runtime quantity:** baked-in English text remained within the artwork while runtime quantity was independent.
- **Case C - coherent artwork with baked-in Chinese text plus a multi-digit runtime quantity:** baked-in Chinese text remained within the artwork, the full multi-digit runtime quantity was independent, and overlapping bboxes were accepted.

The corresponding `instance_001`, `instance_011`, and `instance_017` JSON files must be checked with `scripts/validate_semantic_decomposition.py` before marking this contract fully frozen. Historical experiment files remain immutable.
