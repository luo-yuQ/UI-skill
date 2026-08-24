# Stage2-A `semantic_decompose` v0.1.2

Status: **contract implemented / taxonomy and caller-identity closure**.

`semantic_decompose` v0.1.2 retains the v0.1 output schema, taxonomy enum, coordinate contract, one-level scope, and the v0.1.1 `decompose` / `stop_as_asset` boundary. It tightens only the role-based boundary between `icon` and `illustration`; caller-owned `node_id` is enforced by the engineering normalization layer.

This file is the production visual-model prompt and short behavior reference for the already-selected Stage2-A strategy:

```text
component / component_instance
-> semantic_decompose
```

It does not select a strategy, classify `node_role`, traverse a tree, schedule another branch, or extract pixels.

## Production prompt

You are performing Stage2-A `semantic_decompose` v0.1.2 on exactly one current node. The caller has already established that `node_role` is `component` or `component_instance`.

Your job is to reason about **visual UI component composition**: decide whether the current node is one atomic foundational UI component or is composed of multiple visually distinguishable foundational UI components, then identify exactly one level of owned **Direct Children**. Foundational components include panels and background/base layers, button bases, icons, illustrations, independent text, badges or decorative overlays, frames, progress tracks or fills, and independent visual ornaments. Express them only with the frozen taxonomy below; for example, use `decoration` for a badge treatment and `progress_bar` for a distinguishable progress track or fill.

Functional completeness is irrelevant to the decomposition decision. A child does not need to be independently clickable, functionally complete, or obtainable as a clean rectangular crop. It needs to be an owned, visually distinguishable UI component with its own component identity. Do not create children merely to use a taxonomy category or to split incidental painted details inside one atomic artwork.

### Direct-child and ownership rules

- Decompose one level only. Do not describe grandchildren or how an artwork was painted.
- When the node contains two or more owned, visually distinguishable foundational UI components, decompose them even when they jointly form one complete functional control or one semantically coherent asset. Typical composites include `panel/base + icon`, `panel + text`, `panel + icon + text`, `icon + badge`, and `background + foreground illustration`.
- Do NOT stop decomposition merely because the image forms one complete functional UI control or one semantically coherent asset. In particular, none of these is a valid reason for `stop_as_asset`: "It is already a complete button.", "The elements form one functional asset.", "The illustration is part of the same button.", or "The composition is semantically unified."
- Protect coherent atomic artwork. Keep an illustration or icon whole when its border, glow, shadow, internal symbol, painted text, label plate, local decoration, highlight, or texture jointly forms that one artwork. This protection does not merge a containing panel/base with a visually distinguishable icon, illustration, text layer, badge, or overlay placed on it.
- Decompose foundational UI components, not every visible detail.
- Context padding may show neighboring UI, but context is visible, not owned. Never claim a neighboring slot, text, icon, frame, or background as a child of the current component.
- A parent-level shared background is not owned by the current component.

### Text semantics

- Keep lettering inside one atomic logo, emblem, icon, or illustration when it is integral to that coherent artwork rather than a visually distinguishable component. OCR readability alone does not make internal lettering an independent `text` asset.
- A visually distinguishable label or value placed on a panel/base or button base is a component-level `text` child even when the screenshot cannot prove runtime mutability or the pixels appear baked together. Pixel extraction difficulty belongs to the later extraction stage and must not merge `panel + text` into one atomic asset.
- Emit `taxonomy: text` when lettering or numbers form such a distinguishable component, plausibly change at runtime, or carry independent runtime data such as a quantity, count, or status value.
- Decide from component/layer semantics, not from language, alphabet, whether the content is numeric, or whether the screenshot pixels are already composited.
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

Do not add or rename categories. Map a badge treatment or decorative overlay to `decoration`, and a distinguishable progress track or fill to `progress_bar`. For a composite button whose rounded base is visually distinguishable from its content, classify the owned containing base with the closest existing base/container category, normally `panel`; do not classify the unsplit composite as `button` merely because it is clickable.

### Icon versus illustration: component role first

Classify `icon` versus `illustration` by the visual subject's **UI component role**, not by artwork complexity.

- `icon` is a localized visual component that represents a discrete object, item, action, status, resource, ability, category, or symbolic concept inside a UI composition. It commonly appears inside a panel, button, slot, or card. A potion bottle, sword or weapon graphic, coin or gem, character portrait used as an avatar, skill symbol, and inventory item are `icon` components when they play that localized role.
- Detailed rendering does not turn an icon into an illustration. Glow, particles, leaves, magic effects, highlights, small ornaments, and intricate painting may remain internal details of the same icon. A detailed glowing coin graphic with particles is still an `icon`; a complex localized fire, lightning, or magic skill graphic is still an `icon`.
- `illustration` is a larger artwork-like visual region whose primary role is decorative, narrative, scenic, promotional, or content presentation rather than representing one localized UI symbol or object. Character-plus-environment promotional artwork, a large fantasy scene on a card, multi-object decorative artwork, and a narrative composition containing a character, building, sky, and environment are `illustration` components.
- When both categories seem plausible, ask what component role the subject serves in the current UI. Do not ask whether it is simple or highly rendered. Bbox size is only weak supporting evidence: a large avatar can still be an `icon`, and a small artwork-like scene can still be an `illustration`. Do not use a fixed bbox-area percentage threshold.

#### Role-boundary example

Input:

```text
A detailed sprouting potion bottle graphic is centered on top of a green rounded panel.
```

Incorrect:

```text
stop_as_asset because it forms one complete button.
```

Correct decomposition:

```text
decompose

children:
- green rounded base -> panel
- potion/bottle graphic -> icon
```

The potion graphic remains an `icon` even if it is highly rendered, glowing, decorative, or contains small leaves, particles, or effects, because it acts as a localized item/symbol component inside the UI. Do not classify it as `illustration` merely because of visual complexity.

`Button` describes the complete control's function. `Panel + Icon` describes its visual UI component composition, which is the responsibility of this router. The panel child may cover approximately the whole parent while the icon child is nested inside it; that spatial overlap does not change the decision.

### Frame rule

The rectangular Node Crop is not evidence of a `frame`. Emit a frame only when visible evidence shows a frame owned by this node that can reasonably exist independently or has independent UI/state meaning, such as a selected-state frame. A semantic child bbox may approach or equal the parent bbox; do not apply structural `ineffective_split` rules here.

### Decision

Return exactly one decision:

- `decompose`: the current node contains two or more owned, visually distinguishable foundational UI components. Return each direct component child and omit `asset_taxonomy`.
- `stop_as_asset`: the current node is already one atomic foundational UI component and cannot reasonably be separated into multiple component-level children. Return `children: []` and classify the current node itself with `asset_taxonomy`. A single bottle icon, a single plain rounded panel/base with no independent icon, text, badge, or overlay, and a single coherent decorative illustration are examples.

Do not use functional completeness, shared button membership, semantic unity, or bbox overlap to justify `stop_as_asset`. Recursion is not an obligation for an atomic component, and you must never manufacture a child from incidental visual details merely to perform the strategy.

### Bbox completeness

Every child `bbox` is an integer-pixel rectangle in the provided **Analysis Image** coordinate space, with top-left origin. Its purpose is to enclose the visual asset's complete visible extent, not merely point near it. Include the asset's complete outer contour, edges, full character strokes, shadow, glow, outer highlight, and antialiased edge. Prefer a small safety margin over visible clipping. Normal VLM or rounding variation of about one pixel is acceptable.

`bbox overlap is allowed`. A panel/base child may cover approximately the whole parent while an icon or text child lies inside it. Do not force bboxes to be mutually exclusive, shrink their true visual extents, reject an otherwise valid component decomposition, or change the decision to `stop_as_asset` to avoid overlap. Rectangle intersection does not prove shared pixels. Foreground occlusion, masks, segmentation, inpainting, extraction, and missing-pixel repair belong to later stages and must not be solved here.

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
      "id": "base_001",
      "label": "green rounded base",
      "taxonomy": "panel",
      "bbox": {
        "x": 0,
        "y": 0,
        "width": 1024,
        "height": 512
      },
      "partial": false,
      "confidence": 0.9
    },
    {
      "id": "icon_001",
      "label": "detailed sprouting potion bottle icon",
      "taxonomy": "icon",
      "bbox": {
        "x": 420,
        "y": 116,
        "width": 184,
        "height": 280
      },
      "partial": false,
      "confidence": 0.9
    }
  ],
  "reason": "The node contains a visually distinguishable panel base and icon."
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
  "asset_taxonomy": "icon",
  "children": [],
  "reason": "The node is one atomic bottle icon with no separate component-level children."
}
```

Do not return masks, crops, extraction strategies, repaired geometry, recursive descendants, branch schedules, or any taxonomy outside the frozen ten.

## v0.1.2 behavior summary

- Scope is one already-selected `component` or `component_instance` and one direct-child level.
- The unit of decomposition is a visually distinguishable foundational UI component; functional completeness and semantic unity do not make a composite node atomic.
- A node with multiple owned components such as a panel/base plus icon is decomposed, while a single icon, panel, or coherent illustration stops as an atomic asset.
- Coherent artwork remains protected from visual-detail over-splitting inside one atomic icon or illustration.
- `icon` versus `illustration` is decided by localized UI component role versus artwork-like narrative, scenic, decorative, or content-presentation role; visual complexity and bbox size are not hard classifiers.
- `node_id` remains caller-owned and is overwritten by the engineering normalization layer rather than trusted from model output.
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
