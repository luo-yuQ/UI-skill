# BBox Boundary Tolerance v0.1

Status: **IMPLEMENTED**.

This policy absorbs only small visual-model quantization errors at the outer boundary of the actual Analysis Image. It does not relax any frozen strategy validator and is not a final extraction-bbox accuracy policy.

## Production flow

```text
VLM raw response
-> existing response parsing
-> actual Analysis Image size canonicalization
-> BBox Boundary Canonicalizer
-> existing frozen strategy validator
-> Runtime
```

The tolerance is fixed at 4 Analysis Image pixels. `structural_split`, `expand_instances`, and `semantic_decompose` participate; Router does not emit bboxes and does not participate.

For an actual Analysis Image of width `W` and height `H`, a bbox is eligible only when all four raw edges satisfy:

```text
left >= -4
top >= -4
right <= W + 4
bottom <= H + 4
```

The bbox must also have positive-area intersection with the Analysis Image. An eligible bbox is deterministically clamped to `[0, W] x [0, H]`, then `x`, `y`, `width`, and `height` are recomputed. The final width and height must remain positive.

Eligibility is atomic per bbox. If any edge exceeds the tolerance, the bbox has invalid field types or non-positive raw size, or clamping would not produce positive area, the raw bbox remains unchanged so the frozen validator preserves its existing strict failure behavior. Fully in-bounds bboxes are value-equivalent no-ops.

The canonicalizer uses only the actual Analysis Image file dimensions. It does not use model-reported canvas dimensions, Node Crop dimensions, taxonomy, filename, or image-specific rules.

## Diagnostics

When at least one bbox is canonicalized, ProductionVisualAdapter writes a strategy-specific sidecar beside the Analysis Image:

```text
<strategy>-bbox-boundary-canonicalization.json
```

The sidecar records the actual image size, fixed tolerance, JSON path, item ID when available, raw bbox, canonical bbox, and per-edge raw value, canonical value, and `delta_px`. Diagnostics are never inserted into the frozen strategy result.

