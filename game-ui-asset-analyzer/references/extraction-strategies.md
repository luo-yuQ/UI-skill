# Extraction Strategies v0.1

Use exactly one strategy:

| Strategy | Meaning |
| --- | --- |
| `direct_crop` | The bbox can be used as a reliable rectangular crop for the independent asset. |
| `do_not_extract` | The visual element is recognized but should not become an independent image asset. |
| `advanced_required` | The element should become an independent asset, but a rectangular crop cannot reliably produce it in v0.1. |

Do not introduce implementation-specific strategies such as SAM, masks, inpainting, or repair.

## Issues

Use only these structured issue values:

- `text_baked_in`: text is visually fused with an otherwise reusable asset.
- `complex_background`: surrounding or internal background prevents a clean rectangular crop.
- `occluded`: another visible element covers part of the candidate.
- `partial_visibility`: the candidate extends outside the image or is otherwise only partly shown.
- `merged_with_neighbor`: the candidate cannot be cleanly separated from an adjacent visual element.
- `uncertain_boundary`: the exact asset edge cannot be located reliably.

The `issues` array may be empty except for `advanced_required`, which must have at least one issue. Do not repeat an issue.

## Consistency rules

- `should_extract: false` requires `do_not_extract`.
- `do_not_extract` requires `should_extract: false`.
- `direct_crop` requires `should_extract: true`.
- `advanced_required` requires `should_extract: true` and at least one issue.

Write `reason` as a concise natural-language QA note. Never use it as machine input. Machine decisions may depend only on `semantic_type`, `bbox`, `should_extract`, `strategy`, `issues`, and optional `source_ref`.
