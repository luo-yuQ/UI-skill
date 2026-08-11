# UI Compose Plan v2.1.1

Return one candidate `ui-compose-plan.json` conforming to `schemas/ui-compose-plan.schema.json`. The v2 top-level structure remains unchanged.

## Decision origins

Layout decisions:

- `layout_reference`: real `source_kind`, one or more real A `source_ids`, and source meaning are required.
- `user_requirement`: `source_kind: null`, `source_ids: []`, `source_meaning: null`.
- `composer_derived`: the same empty A fields; limited to low-risk UI completion.

Style decisions:

- `style_reference`: real B `trait_id`, dimension, and classification are required.
- `user_requirement`: null trait/classification.
- `composer_derived`: null trait/classification and no claim of B evidence.

Not every new design decision needs A/B evidence. The origin must describe where it actually came from.

## A-first layout synthesis

Build the target layout from A major regions, relationships, hierarchy, repeat directions, and approximate proportions before mapping target business components. Record one explicit disposition for every high-confidence A major region. Prefer `adapted` when the source business content is irrelevant but the spatial role transfers. Use `ignored` only for a precise conflict with user semantics, counts, grids, or an explicitly locked position.

In `hard_requirements.required_elements`, use a non-null `position` only for an explicitly immutable user position. Use `position: null` for a required element whose directional wording is merely a soft preference. The final layout may still occupy that direction because A supports it; its origin remains `layout_reference`, not a fabricated user hard lock.

## Repeat contract

Every `component_tree[].repeat` contains `count`, `arrangement`, `columns`, `rows`, and `content_variation`.

- For `row`, `column`, `list`, `carousel`, or `custom`, use `columns: null` and `rows: null`.
- For `grid`, use positive integer `columns` and `rows`, and make `count` equal `columns * rows`.

Do not encode a row as `columns=count, rows=1` or a column as `columns=1, rows=count`; those are non-grid arrangements and must keep both grid dimensions null.

Also keep prose and geometry consistent: vertical stacks use `column`, horizontal bands use `row`, and grid language uses `grid`. The component `design_intent`, A repeat direction, and layout relationships must agree with the repeat contract.

## Evidence Registry validation

`scripts/evidence_registry.py` recursively reads the actual A/B objects into frozen Pydantic records containing IDs, types/classifications, and JSON paths. It never mutates upstream data.

`scripts/validate_plan.py` enforces:

- source ID membership only for `origin=layout_reference`;
- trait membership and exact B classification only for `origin=style_reference`;
- exact error paths and invalid values;
- no automatic correction or semantic ID mapping;
- hard requirements, local scope, and cross-section consistency.

```powershell
python scripts/validate_plan.py <candidate-plan.json> --input <input.json>
```

The result is PASS or FAIL. Repair and retry loops are outside V2.1.1.

## Known issue

Parent-relative child anchors may be misread by the existing required-position check when the parent owns left/right placement. This version records the issue without redesigning layout-parent validation.
